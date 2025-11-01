import os
from pathlib import Path
from typing import List, Tuple, Optional
import logging
from PyPDF2 import PdfReader, PdfWriter
from shared.minio_client import get_minio_client

logger = logging.getLogger(__name__)


class PDFSplitter:
    """Divide PDFs em páginas individuais para processamento paralelo"""

    def __init__(self, temp_dir: Path):
        """
        Args:
            temp_dir: Diretório temporário para salvar páginas
        """
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def is_pdf(self, file_path: Path) -> bool:
        """Verifica se arquivo é PDF"""
        return file_path.suffix.lower() == '.pdf'

    def get_page_count(self, pdf_path: Path) -> int:
        """Retorna número de páginas do PDF"""
        try:
            reader = PdfReader(str(pdf_path))
            return len(reader.pages)
        except Exception as e:
            logger.error(f"Erro ao contar páginas: {e}")
            raise

    def split_pdf(self, pdf_path: Path, job_id: Optional[str] = None, upload_to_minio: bool = True) -> List[Tuple[int, Path, Optional[str]]]:
        """
        Divide PDF em páginas individuais e opcionalmente faz upload para MinIO

        Args:
            pdf_path: Caminho do PDF original
            job_id: ID do job (usado para organizar no MinIO)
            upload_to_minio: Se True, faz upload das páginas para MinIO

        Returns:
            Lista de tuplas (page_number, local_page_path, minio_path)
        """
        if not self.is_pdf(pdf_path):
            raise ValueError(f"Arquivo não é PDF: {pdf_path}")

        logger.info(f"Dividindo PDF: {pdf_path}")

        try:
            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)

            logger.info(f"PDF tem {total_pages} páginas")

            page_files = []
            minio_client = get_minio_client() if upload_to_minio else None

            for page_num in range(total_pages):
                # Criar writer para página única
                writer = PdfWriter()
                writer.add_page(reader.pages[page_num])

                # Salvar página individual localmente
                page_filename = f"page_{page_num + 1:04d}.pdf"
                page_path = self.temp_dir / page_filename

                with open(page_path, 'wb') as output_file:
                    writer.write(output_file)

                # Upload para MinIO se habilitado
                minio_path = None
                if minio_client and job_id:
                    minio_object_name = f"pages/{job_id}/{page_filename}"
                    try:
                        minio_client.upload_file(
                            bucket_name=minio_client.bucket_pages,
                            object_name=minio_object_name,
                            file_path=str(page_path),
                            content_type="application/pdf",
                        )
                        minio_path = minio_object_name
                        logger.debug(f"Página {page_num + 1} enviada para MinIO: {minio_path}")
                    except Exception as e:
                        logger.error(f"Erro ao enviar página {page_num + 1} para MinIO: {e}")
                        # Continua mesmo com erro no MinIO

                page_files.append((page_num + 1, page_path, minio_path))
                logger.debug(f"Página {page_num + 1}/{total_pages} salva: {page_path}")

            logger.info(f"PDF dividido em {len(page_files)} páginas")
            return page_files

        except Exception as e:
            logger.error(f"Erro ao dividir PDF: {e}", exc_info=True)
            raise

    def extract_single_page(self, pdf_path: Path, page_number: int, job_id: Optional[str] = None, upload_to_minio: bool = True) -> Tuple[Path, Optional[str]]:
        """
        Extrai uma página específica do PDF e opcionalmente faz upload para MinIO

        Args:
            pdf_path: Caminho do PDF original
            page_number: Número da página (1-indexed)
            job_id: ID do job (usado para organizar no MinIO)
            upload_to_minio: Se True, faz upload da página para MinIO

        Returns:
            Tupla (local_page_path, minio_path)
        """
        if not self.is_pdf(pdf_path):
            raise ValueError(f"Arquivo não é PDF: {pdf_path}")

        logger.info(f"Extraindo página {page_number} de {pdf_path}")

        try:
            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)

            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"Número de página inválido: {page_number}. PDF tem {total_pages} páginas.")

            # Criar writer para página única (page_number é 1-indexed, mas reader.pages é 0-indexed)
            writer = PdfWriter()
            writer.add_page(reader.pages[page_number - 1])

            # Salvar página individual localmente
            page_filename = f"page_{page_number:04d}.pdf"
            page_path = self.temp_dir / page_filename

            with open(page_path, 'wb') as output_file:
                writer.write(output_file)

            # Upload para MinIO se habilitado
            minio_path = None
            if upload_to_minio and job_id:
                minio_client = get_minio_client()
                minio_object_name = f"pages/{job_id}/{page_filename}"
                try:
                    minio_client.upload_file(
                        bucket_name=minio_client.bucket_pages,
                        object_name=minio_object_name,
                        file_path=str(page_path),
                        content_type="application/pdf",
                    )
                    minio_path = minio_object_name
                    logger.debug(f"Página {page_number} enviada para MinIO: {minio_path}")
                except Exception as e:
                    logger.error(f"Erro ao enviar página {page_number} para MinIO: {e}")

            logger.info(f"Página {page_number} extraída: {page_path}")
            return page_path, minio_path

        except Exception as e:
            logger.error(f"Erro ao extrair página {page_number}: {e}", exc_info=True)
            raise

    def cleanup_pages(self, page_files: List[Tuple[int, Path, Optional[str]]], cleanup_minio: bool = True):
        """
        Remove arquivos de páginas temporárias do filesystem e opcionalmente do MinIO

        Args:
            page_files: Lista de tuplas (page_number, local_path, minio_path)
            cleanup_minio: Se True, remove também do MinIO
        """
        minio_client = get_minio_client() if cleanup_minio else None

        for page_data in page_files:
            # Handle both old format (page_num, path) and new format (page_num, path, minio_path)
            if len(page_data) == 2:
                page_num, page_path = page_data
                minio_path = None
            else:
                page_num, page_path, minio_path = page_data

            # Cleanup local file
            try:
                if page_path.exists():
                    page_path.unlink()
                    logger.debug(f"Página local removida: {page_path}")
            except Exception as e:
                logger.warning(f"Erro ao remover página local {page_path}: {e}")

            # Cleanup MinIO file
            if minio_client and minio_path:
                try:
                    minio_client.delete_file(
                        bucket_name=minio_client.bucket_pages,
                        object_name=minio_path
                    )
                    logger.debug(f"Página removida do MinIO: {minio_path}")
                except Exception as e:
                    logger.warning(f"Erro ao remover página do MinIO {minio_path}: {e}")


def should_split_pdf(file_path: Path, min_pages: int = 2) -> bool:
    """
    Verifica se PDF deve ser dividido

    Args:
        file_path: Caminho do arquivo
        min_pages: Número mínimo de páginas para dividir (default: 2)

    Returns:
        True se deve dividir, False caso contrário
    """
    if not file_path.suffix.lower() == '.pdf':
        return False

    try:
        reader = PdfReader(str(file_path))
        page_count = len(reader.pages)
        return page_count >= min_pages
    except Exception as e:
        logger.warning(f"Erro ao verificar PDF: {e}")
        return False
