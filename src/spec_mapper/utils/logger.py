"""Logging configuration and utilities."""

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: str | None = None, console_output: bool = True) -> None:
    """Setup logging configuration with file and console output.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Path to log file. If None, uses default: logs/als2spec_<timestamp>.log
        console_output: Whether to also output to console

    Examples:
        >>> # Log to console and file
        >>> setup_logging("INFO", "logs/run.log")

        >>> # Log to file only
        >>> setup_logging("DEBUG", "logs/debug.log", console_output=False)

        >>> # Log to console only (no file)
        >>> setup_logging("INFO", log_file=None, console_output=True)
    """
    # Convert level string to numeric level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")

    # Create formatters
    detailed_formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    simple_formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Add file handler if log_file specified
    if log_file:
        log_path = Path(log_file)

        # Create logs directory if it doesn't exist
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(
            log_path,
            mode="w",  # Overwrite existing log file
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(file_handler)

        try:
            print(f"📝 Logging to file: {log_path.absolute()}")
        except UnicodeEncodeError:
            print(f"Logging to file: {log_path.absolute()}")

    # Add console handler if requested
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(simple_formatter)
        root_logger.addHandler(console_handler)

    # Log the start of logging
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info(f"Logging initialized at level: {level}")
    if log_file:
        logger.info(f"Log file: {log_path.absolute()}")
    logger.info("=" * 80)


def get_default_log_filename() -> str:
    """Get default log filename with timestamp.

    Returns:
        Log filename like "logs/als2spec_20251023_143022.log"
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"logs/als2spec_{timestamp}.log"
