"""Configuration loader utility."""

import logging
from pathlib import Path
from typing import Any, ClassVar

import yaml

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* onto *base* (override wins for scalars/lists)."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigLoader:
    """Load and manage configuration from YAML file.

    Examples:
        >>> config = ConfigLoader()
        >>> als_columns = config.get('als_columns')
        >>> table_col = als_columns['table']
    """

    _instances: ClassVar[dict[str, "ConfigLoader"]] = {}
    _config: dict[str, Any]
    _initialized: bool

    def __new__(cls, config_path: str | Path | None = None) -> "ConfigLoader":
        """Implement singleton pattern per config path.

        Args:
            config_path: Path to the configuration YAML file
        """
        # Use default path if not specified
        if config_path is None:
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config" / "config.yaml"
        else:
            config_path = Path(config_path)

        # Create instance key based on config path
        key = str(config_path.resolve())

        # Return existing instance or create new one
        if key not in cls._instances:
            instance = super().__new__(cls)
            instance._config = {}
            instance._initialized = False
            cls._instances[key] = instance

        return cls._instances[key]

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize configuration loader.

        Args:
            config_path: Path to the configuration YAML file.
                        If None, uses default path: config/config.yaml
        """
        # Only load config once per instance
        if self._initialized:
            return

        self._initialized = True

        if config_path is None:
            # Default path relative to project root
            project_root = Path(__file__).parent.parent
            config_path = project_root / "config" / "config.yaml"
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            # Log only the file name (never the absolute path): this may run
            # inside a user-downloadable job log.
            logger.warning(f"Config file not found: {config_path.name}, using defaults")
            self._load_defaults()
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            # Support a single-level "_extends" so a small variant config (e.g.
            # config_ig34.yaml) can inherit the full base config and only declare
            # its overrides, instead of duplicating hundreds of lines.
            base_name = self._config.pop("_extends", None)
            if base_name:
                base_path = (config_path.parent / base_name).resolve()
                if base_path.exists():
                    with open(base_path, encoding="utf-8") as bf:
                        base_config = yaml.safe_load(bf) or {}
                    self._config = _deep_merge(base_config, self._config)
                    logger.info(f"Configuration loaded from {config_path.name} (extends {base_name})")
                else:
                    logger.warning(f"_extends base not found: {base_path.name}; using {config_path.name} alone")
            else:
                logger.info(f"Configuration loaded from {config_path.name}")
        except Exception as e:
            # Never log the raw exception text (may embed the absolute path).
            logger.error("Failed to load config '%s' (%s)", config_path.name, type(e).__name__)
            self._load_defaults()

    def _load_defaults(self) -> None:
        """Load default configuration if YAML file is not available."""
        self._config = {
            "als_columns": {
                # Primary column names (Chinese, from sdtm_processor.py output)
                "table": "表",
                "variable": "变量",
                "variable_label": "变量名",
                "sdtm_domain": "SDTM_Domain",
                "sdtm_variable": "SDTM_Variable",
                # Fallback column names (English, for backwards compatibility)
                "table_en": "Metadata Table",
                "variable_en": "Metadata Variable",
                "variable_label_en": "Annotation Variable",
                "sdtm_domain_en": "SDTM Domain",
                "sdtm_variable_en": "Representative Variable",
            },
            "als_defaults": {
                "sheet_name": "Sheet1",
                "header_row": 0,
            },
            "template_columns": {
                "variable_name": "变量名称",
                "transformation_def": "转换定义",
            },
            "template_defaults": {
                "header_row": 12,
                "min_sheet_name_length": 2,
                "max_sheet_name_length": 2,
            },
            "output_format": {
                "prefix": "[RAW]",
                "multi_item_format": "{index}. {value}",
                "separator": "\n",
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key.

        Args:
            key: Configuration key (supports nested keys with dot notation)
            default: Default value if key not found

        Returns:
            Configuration value or default

        Examples:
            >>> config = ConfigLoader()
            >>> config.get('als_columns.table')
            '表'
            >>> config.get('nonexistent', 'default_value')
            'default_value'
        """
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_als_columns(self) -> dict[str, str]:
        """Get ALS column mappings.

        Returns:
            Dictionary of column name mappings
        """
        return self.get("als_columns", {})

    def get_template_columns(self) -> dict[str, str]:
        """Get template column mappings.

        Returns:
            Dictionary of column name mappings
        """
        return self.get("template_columns", {})

    def get_output_format(self) -> dict[str, str]:
        """Get output format settings.

        Returns:
            Dictionary of output format settings
        """
        return self.get("output_format", {})
