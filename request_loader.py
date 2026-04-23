#!/usr/bin/env python3
"""
RoboScout Query Generator — Request Loader
============================================
Load partnering request data from Snowflake or CLI arguments.
Reuses connection pattern from Proposal_Fit_Score/snowflake_pipeline.py.
"""

import logging
import os
import re
from typing import List

from config import settings
from models_roboscout import QueryRequest

logger = logging.getLogger("roboscout_query_gen.request_loader")


class RequestLoader:
    """Load and normalize partnering request data."""

    def __init__(self, use_sso: bool = False):
        self.use_sso = use_sso
        self._conn = None

    def load_from_snowflake(self, request_id: int) -> QueryRequest:
        """Load request from Snowflake OPS_REQUEST_DATA view."""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Try with OUT_OF_SCOPE and REQUESTABLE_TYPE columns first, fall back without them
            try:
                cursor.execute(
                    """
                    SELECT
                        ID,
                        TITLE,
                        LOOKING_FOR,
                        USE_CASE,
                        SOLUTIONS_OF_INTEREST,
                        REQUEST_REQUIREMENTS,
                        OUT_OF_SCOPE,
                        PARTNER_TYPES,
                        TRL_RANGE,
                        REQUESTABLE_TYPE
                    FROM OPS_REQUEST_DATA
                    WHERE ID = %s
                    """,
                    [request_id],
                )
            except Exception:
                logger.info("OUT_OF_SCOPE/REQUESTABLE_TYPE columns not available, using fallback query")
                cursor.execute(
                    """
                    SELECT
                        ID,
                        TITLE,
                        LOOKING_FOR,
                        USE_CASE,
                        SOLUTIONS_OF_INTEREST,
                        REQUEST_REQUIREMENTS,
                        PARTNER_TYPES,
                        TRL_RANGE
                    FROM OPS_REQUEST_DATA
                    WHERE ID = %s
                    """,
                    [request_id],
                )

            row = cursor.fetchone()
            if not row:
                raise ValueError(f"No request found with ID {request_id}")

            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row))

            # Only run on Rfp-type requests
            requestable_type = (data.get("REQUESTABLE_TYPE", "") or "").strip()
            if requestable_type and requestable_type != "Rfp":
                raise ValueError(
                    f"Request {request_id} is type '{requestable_type}', not 'Rfp'. "
                    f"Skipping — RoboScout only runs on Rfp requests."
                )

            # Fetch must-have requirements
            must_haves = self._fetch_must_have_requirements(request_id)

            # Use dedicated column if available, fall back to regex parsing
            req_text = data.get("REQUEST_REQUIREMENTS", "") or ""
            out_of_scope = (data.get("OUT_OF_SCOPE", "") or "").strip()
            if not out_of_scope:
                out_of_scope = self._parse_out_of_scope(req_text)

            request = QueryRequest(
                request_id=request_id,
                title=data.get("TITLE", "") or "",
                looking_for=data.get("LOOKING_FOR", "") or "",
                use_case=data.get("USE_CASE", "") or "",
                solutions_of_interest=data.get("SOLUTIONS_OF_INTEREST", "") or "",
                requirements=req_text,
                out_of_scope=out_of_scope,
                partner_types=data.get("PARTNER_TYPES", "") or "",
                trl_range=data.get("TRL_RANGE", "") or "",
                must_have_requirements=must_haves,
            )

            logger.info(f"Loaded request {request_id}: '{request.title}'")
            return request

        finally:
            cursor.close()

    # Internal/test company IDs to exclude from automated runs.
    # Defaults live in config.Settings.excluded_company_ids and can be
    # overridden via ROBOSCOUT_EXCLUDED_COMPANY_IDS env var without a code edit.
    @property
    def EXCLUDED_COMPANY_IDS(self) -> List[int]:
        return list(settings.excluded_company_ids)

    def find_new_requests(self, hours: int = 24) -> list:
        """Find requests launched in the last N hours that are enabled and complete.

        Excludes internal/test companies (configured in settings.excluded_company_ids).

        Returns list of dicts: [{"id": 1597, "title": "...", "launch_date": "..."}]
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            excluded = self.EXCLUDED_COMPANY_IDS
            placeholders = ",".join(["%s"] * len(excluded)) if excluded else "NULL"
            cursor.execute(
                f"""
                SELECT
                    ID,
                    TITLE,
                    REQUEST_LAUNCH_DATE,
                    COMPANY_NAME
                FROM OPS_REQUEST_DATA
                WHERE ENABLED = TRUE
                  AND COMPLETE = TRUE
                  AND REQUEST_LAUNCH_DATE >= DATEADD(hour, -%s, CURRENT_TIMESTAMP())
                  AND DELETED_AT IS NULL
                  AND COMPANY_ID NOT IN ({placeholders})
                  AND VISIBILITY_MASK >= 4
                  AND (REQUESTABLE_TYPE = 'Rfp' OR REQUESTABLE_TYPE IS NULL)
                ORDER BY REQUEST_LAUNCH_DATE DESC
                """,
                [hours] + excluded,
            )

            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            results = []
            for row in rows:
                data = dict(zip(columns, row))
                results.append({
                    "id": data["ID"],
                    "title": data.get("TITLE", ""),
                    "launch_date": str(data.get("REQUEST_LAUNCH_DATE", "")),
                    "company": data.get("COMPANY_NAME", ""),
                })

            logger.info(f"Found {len(results)} new requests in last {hours} hours")
            return results

        finally:
            cursor.close()

    def load_from_args(
        self,
        looking_for: str = "",
        use_case: str = "",
        sois: str = "",
        title: str = "",
        requirements: str = "",
        out_of_scope: str = "",
    ) -> QueryRequest:
        """Build QueryRequest from CLI arguments."""
        request = QueryRequest(
            title=title or f"Manual query: {looking_for[:60]}",
            looking_for=looking_for,
            use_case=use_case,
            solutions_of_interest=sois,
            requirements=requirements,
            out_of_scope=out_of_scope,
        )
        logger.info(f"Built request from CLI args: '{request.title}'")
        return request

    def _fetch_must_have_requirements(self, request_id: int) -> List[str]:
        """Fetch must-have requirements from dedicated table."""
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT REQUIREMENT_TEXT
                FROM OPS_REQUEST_REQUIREMENTS
                WHERE REQUEST_ID = %s
                AND IS_MUST_HAVE = TRUE
                ORDER BY DISPLAY_ORDER
                """,
                [request_id],
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception:
            # Table may not exist or have different schema
            return []
        finally:
            cursor.close()

    def _parse_out_of_scope(self, requirements_text: str) -> str:
        """Extract out-of-scope items from requirements text."""
        if not requirements_text:
            return ""

        # Look for "out of scope" section
        patterns = [
            r"(?i)out[\s-]of[\s-]scope[:\s]*(.*?)(?=\n\n|\Z)",
            r"(?i)not[\s-]in[\s-]scope[:\s]*(.*?)(?=\n\n|\Z)",
            r"(?i)excluded?[:\s]*(.*?)(?=\n\n|\Z)",
        ]

        for pattern in patterns:
            match = re.search(pattern, requirements_text, re.DOTALL)
            if match:
                return match.group(1).strip()

        return ""

    def _get_connection(self):
        """Get or create Snowflake connection."""
        if self._conn is not None:
            return self._conn

        import snowflake.connector

        common = dict(
            account=os.getenv("SNOWFLAKE_ACCOUNT", "HCXAKRI-TJB53055"),
            user=os.getenv("SNOWFLAKE_USER", "NEIL"),
            warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "FIVETRAN_WAREHOUSE"),
            database=os.getenv("SNOWFLAKE_DATABASE", "FIVETRAN_DATABASE"),
            schema=os.getenv("SNOWFLAKE_SCHEMA", "HEROKU_POSTGRES_PUBLIC"),
            role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        )

        if self.use_sso:
            self._conn = snowflake.connector.connect(
                authenticator="externalbrowser",
                **common,
            )
        else:
            self._conn = snowflake.connector.connect(
                password=os.getenv("SNOWFLAKE_PASSWORD"),
                **common,
            )

        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
