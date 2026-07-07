"""
Knowledge Base Manager for iSDTaiM

This module provides high-level management functionality for the knowledge base,
including data loading, querying, and maintenance operations.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class KnowledgeBaseManager:
    """
    High-level manager for knowledge base operations.

    This class provides functionality to manage SDTM knowledge base data,
    including loading, querying, and maintaining Parquet files.
    """

    def __init__(self, knowledge_base_path: str = "data/knowledge_base"):
        """
        Initialize the knowledge base manager.

        Args:
            knowledge_base_path: Path to the knowledge base directory
        """
        self.kb_path = Path(knowledge_base_path)
        self.processed_path = self.kb_path / "structured"
        self.documents_path = self.kb_path / "documents"

        # Ensure directories exist
        self.processed_path.mkdir(parents=True, exist_ok=True)

        logger.info("KnowledgeBaseManager initialized")
        logger.info(f"Knowledge base path: {self.kb_path}")

    def load_sdtm_standards(self, domain: str | None = None) -> pd.DataFrame:
        """
        Load SDTM standards data from Parquet files.

        Args:
            domain: Optional domain filter (e.g., 'AE', 'DM', 'LB')

        Returns:
            DataFrame containing SDTM standards data
        """
        parquet_file = self.processed_path / "sdtm_spec_enhanced.parquet"  # 使用增强版 SDTM 规范

        if not parquet_file.exists():
            raise FileNotFoundError(f"SDTM standards Parquet file not found: {parquet_file}")

        df_raw = pd.read_parquet(parquet_file)
        # 增强版文件已经是清洁数据，不需要额外过滤

        meta_records: list[dict[str, Any]] = []
        for row in df_raw.to_dict("records"):
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            record = {
                "Dataset Name": row.get("domain", ""),
                "Variable Name": row.get("variable", ""),
                "Variable Label": row.get("label", metadata.get("label", "")),
                "Role": row.get("role", metadata.get("role", "")),
                "Core": row.get("core", metadata.get("core", "")),
                "Class": row.get("class", metadata.get("class", "")),
                "Structure": row.get("structure", metadata.get("structure", "")),
                "Type": metadata.get("type", ""),
                "Variable Order": metadata.get("variable_order", row.get("variable_order", 0)),
            }
            meta_records.append(record)

        df = pd.DataFrame(meta_records)

        if domain:
            df = df[df["Dataset Name"] == domain]
            logger.info(f"Filtered to domain '{domain}': {len(df)} rows")

        return df

    def search_variables(self, query: str, domain: str | None = None, role: str | None = None) -> pd.DataFrame:
        """
        Search for variables in the SDTM standards.

        Args:
            query: Search query (searches in Variable Name and Variable Label)
            domain: Optional domain filter
            role: Optional role filter (e.g., 'Identifier', 'Topic', 'Timing')

        Returns:
            DataFrame with matching variables
        """
        df = self.load_sdtm_standards(domain)

        # Create search mask
        search_mask = df["Variable Name"].str.contains(query, case=False, na=False) | df["Variable Label"].str.contains(
            query, case=False, na=False
        )

        # Apply filters
        if role:
            search_mask = search_mask & (df["Role"] == role)

        results = df[search_mask]
        logger.info(f"Found {len(results)} variables matching query: '{query}'")

        return results

    def get_domain_variables(self, domain: str) -> pd.DataFrame:
        """
        Get all variables for a specific domain.

        Args:
            domain: Domain code (e.g., 'AE', 'DM', 'LB')

        Returns:
            DataFrame with domain variables
        """
        df = self.load_sdtm_standards(domain)

        # Sort by Variable Order
        if "Variable Order" in df.columns:
            df = df.sort_values("Variable Order")

        logger.info(f"Retrieved {len(df)} variables for domain: {domain}")
        return df

    def get_required_variables(self, domain: str) -> pd.DataFrame:
        """
        Get required variables for a specific domain.

        Args:
            domain: Domain code

        Returns:
            DataFrame with required variables only
        """
        df = self.get_domain_variables(domain)

        # Filter for required variables
        required_df = df[df["Core"] == "Req"]

        logger.info(f"Found {len(required_df)} required variables for domain: {domain}")
        return required_df

    def get_variable_info(self, variable_name: str, domain: str | None = None) -> dict[str, Any]:
        """
        Get detailed information about a specific variable.

        Args:
            variable_name: Name of the variable
            domain: Optional domain filter

        Returns:
            Dictionary with variable information
        """
        df = self.load_sdtm_standards(domain)

        # Find the variable
        var_mask = df["Variable Name"] == variable_name
        if domain:
            var_mask = var_mask & (df["Dataset Name"] == domain)

        var_data = df[var_mask]

        if len(var_data) == 0:
            raise ValueError(f"Variable '{variable_name}' not found in domain '{domain}'")

        if len(var_data) > 1:
            logger.warning(f"Multiple matches found for variable '{variable_name}'")

        var_info = var_data.iloc[0].to_dict()

        # Add additional computed fields
        var_info["is_required"] = var_info.get("Core") == "Req"
        var_info["is_identifier"] = var_info.get("Role") == "Identifier"
        var_info["is_timing"] = var_info.get("Role") == "Timing"

        logger.info(f"Retrieved info for variable: {variable_name}")
        return var_info

    def get_domain_summary(self) -> pd.DataFrame:
        """
        Get summary statistics for all domains.

        Returns:
            DataFrame with domain summaries
        """
        df = self.load_sdtm_standards()

        # Group by domain and calculate statistics
        summary = (
            df.groupby("Dataset Name")
            .agg(
                {
                    "Variable Name": "count",
                    "Core": lambda x: (x == "Req").sum(),
                    "Role": lambda x: x.nunique(),
                    "Class": "first",
                }
            )
            .rename(
                columns={
                    "Variable Name": "total_variables",
                    "Core": "required_variables",
                    "Role": "unique_roles",
                    "Class": "class",
                }
            )
        )

        # Calculate percentages
        summary["required_percentage"] = (summary["required_variables"] / summary["total_variables"] * 100).round(1)

        summary = summary.sort_values("total_variables", ascending=False)

        logger.info(f"Generated summary for {len(summary)} domains")
        return summary

    def export_domain_to_csv(self, domain: str, output_path: str | None = None) -> str:
        """
        Export domain variables to CSV file.

        Args:
            domain: Domain code
            output_path: Optional output file path

        Returns:
            Path to the exported CSV file
        """
        df = self.get_domain_variables(domain)

        if output_path is None:
            csv_path = self.processed_path / f"{domain}_variables.csv"
        else:
            csv_path = Path(output_path)

        df.to_csv(csv_path, index=False, encoding="utf-8")

        logger.info(f"Exported {len(df)} variables for domain '{domain}' to {csv_path}")
        return str(csv_path)

    def get_knowledge_base_stats(self) -> dict[str, Any]:
        """
        Get comprehensive statistics about the knowledge base.

        Returns:
            Dictionary with knowledge base statistics
        """
        try:
            df = self.load_sdtm_standards()

            stats = {
                "total_variables": len(df),
                "total_domains": df["Dataset Name"].nunique(),
                "total_classes": df["Class"].nunique(),
                "total_roles": df["Role"].nunique(),
                "required_variables": len(df[df["Core"] == "Req"]),
                "optional_variables": len(df[df["Core"] == "Perm"]),
                "expected_variables": len(df[df["Core"] == "Exp"]),
                "domains": df["Dataset Name"].value_counts().to_dict(),
                "classes": df["Class"].value_counts().to_dict(),
                "roles": df["Role"].value_counts().to_dict(),
                "core_types": df["Core"].value_counts().to_dict(),
            }

            logger.info("Generated knowledge base statistics")
            return stats

        except Exception as e:
            logger.error(f"Error generating statistics: {e!s}")
            return {}

    def validate_domain_structure(self, domain: str) -> dict[str, Any]:
        """
        Validate the structure of a domain against SDTM standards.

        Args:
            domain: Domain code to validate

        Returns:
            Dictionary with validation results
        """
        try:
            df = self.get_domain_variables(domain)

            validation = {
                "domain": domain,
                "total_variables": len(df),
                "required_variables": len(df[df["Core"] == "Req"]),
                "has_studyid": "STUDYID" in df["Variable Name"].values,
                "has_domain": "DOMAIN" in df["Variable Name"].values,
                "has_usubjid": "USUBJID" in df["Variable Name"].values,
                "identifier_variables": df[df["Role"] == "Identifier"]["Variable Name"].tolist(),
                "timing_variables": df[df["Role"] == "Timing"]["Variable Name"].tolist(),
                "topic_variables": df[df["Role"] == "Topic"]["Variable Name"].tolist(),
                "issues": [],
            }

            # Check for common issues
            if not validation["has_studyid"]:
                validation["issues"].append("Missing STUDYID variable")
            if not validation["has_domain"]:
                validation["issues"].append("Missing DOMAIN variable")
            if not validation["has_usubjid"]:
                validation["issues"].append("Missing USUBJID variable")

            logger.info(f"Validated domain '{domain}': {len(validation['issues'])} issues found")
            return validation

        except Exception as e:
            logger.error(f"Error validating domain '{domain}': {e!s}")
            return {"domain": domain, "error": str(e)}


def main():
    """
    Main function for command-line usage.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Base Manager CLI")
    parser.add_argument("--kb-path", default="data/knowledge_base", help="Knowledge base directory path")
    parser.add_argument("--domain", help="Domain code to query")
    parser.add_argument("--search", help="Search query for variables")
    parser.add_argument("--stats", action="store_true", help="Show knowledge base statistics")
    parser.add_argument("--validate", help="Validate domain structure")
    parser.add_argument("--export", help="Export domain to CSV")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    manager = KnowledgeBaseManager(args.kb_path)

    if args.stats:
        # Show statistics
        stats = manager.get_knowledge_base_stats()
        print("\nKnowledge Base Statistics:")
        print(f"Total variables: {stats.get('total_variables', 0)}")
        print(f"Total domains: {stats.get('total_domains', 0)}")
        print(f"Required variables: {stats.get('required_variables', 0)}")

    elif args.validate:
        # Validate domain
        validation = manager.validate_domain_structure(args.validate)
        print(f"\nDomain '{args.validate}' Validation:")
        print(f"Total variables: {validation.get('total_variables', 0)}")
        print(f"Required variables: {validation.get('required_variables', 0)}")
        if validation.get("issues"):
            print(f"Issues: {', '.join(validation['issues'])}")
        else:
            print("No issues found")

    elif args.export:
        # Export domain
        output_file = manager.export_domain_to_csv(args.export)
        print(f"Exported domain '{args.export}' to: {output_file}")

    elif args.search:
        # Search variables
        results = manager.search_variables(args.search, args.domain)
        print(f"\nFound {len(results)} variables matching '{args.search}':")
        for row in results.to_dict("records"):
            print(f"  {row['Variable Name']} ({row['Dataset Name']}) - {row['Variable Label']}")

    elif args.domain:
        variables = manager.get_domain_variables(args.domain)
        print(f"\nDomain '{args.domain}' variables ({len(variables)} total):")
        for row in variables.to_dict("records"):
            core_indicator = "✓" if row["Core"] == "Req" else "○"
            print(f"  {core_indicator} {row['Variable Name']} - {row['Variable Label']}")
    else:
        print("Please specify an action: --stats, --validate, --export, --search, or --domain")


if __name__ == "__main__":
    main()
