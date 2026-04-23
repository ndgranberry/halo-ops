#!/usr/bin/env python3
"""
Agent Scout — Snowflake Client
================================
Loads partnering request context from Snowflake for use in discovery and scoring.

Data lives across 3 tables:
  - REQUESTS: title, use_case, partner_types, slug, requestable_id/type
  - REQUEST_SOLUTIONS: Solutions of Interest (SOIs) for the request
  - REQUEST_REQUIREMENTS: Requirements for the request

The LOOKING_FOR and OUT_OF_SCOPE fields live on REQUEST_FOR_PROPOSALS (linked via
REQUESTABLE_ID), but that table's ID space may not match. When the RFP lookup fails,
we synthesize context from the SOIs and requirements.

Connection uses env vars:
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
  SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA
"""

import json
import logging
import os
from typing import Dict, Any, List, Optional

import snowflake.connector

logger = logging.getLogger(__name__)


def get_snowflake_connection():
    """Create a Snowflake connection from env vars."""
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )


def get_request_data(request_id: int) -> Dict[str, Any]:
    """
    Load full request context from Snowflake.

    Returns a dict with keys:
        TITLE, LOOKING_FOR, USE_CASE, SOLUTIONS_OF_INTEREST,
        PARTNER_TYPES, TRL_RANGE, REQUIREMENTS, OUT_OF_SCOPE

    Query strategy:
    1. REQUESTS table → title, use_case, partner_types, requestable_id
    2. REQUEST_SOLUTIONS → SOIs (filtered by _FIVETRAN_DELETED = FALSE)
    3. REQUEST_REQUIREMENTS → requirements (filtered by _FIVETRAN_DELETED = FALSE)
    4. REQUEST_FOR_PROPOSALS → looking_for, out_of_scope, trl_range (via requestable_id)
       Falls back to synthesizing from SOIs/requirements if RFP lookup fails.
    """
    conn = get_snowflake_connection()
    cur = conn.cursor()

    try:
        # 1. Base request data
        cur.execute("""
            SELECT TITLE, USE_CASE, PARTNER_TYPES, REQUESTABLE_ID, REQUESTABLE_TYPE, SLUG
            FROM REQUESTS
            WHERE ID = %s
        """, (request_id,))
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

        if not row:
            raise ValueError(f"Request {request_id} not found in Snowflake")

        request = dict(zip(columns, row))
        logger.info(f"Loaded request {request_id}: {request.get('TITLE')}")

        # Parse PARTNER_TYPES from JSON array string
        partner_types_raw = request.get("PARTNER_TYPES", "")
        if partner_types_raw and isinstance(partner_types_raw, str):
            try:
                partner_types_list = json.loads(partner_types_raw)
                partner_types = ", ".join(
                    pt.replace("_", " ").title() for pt in partner_types_list
                )
            except json.JSONDecodeError:
                partner_types = partner_types_raw
        else:
            partner_types = ""

        # 2. Solutions of Interest
        cur.execute("""
            SELECT NAME FROM REQUEST_SOLUTIONS
            WHERE REQUEST_ID = %s AND _FIVETRAN_DELETED = FALSE
            ORDER BY ID
        """, (request_id,))
        sois = [row[0] for row in cur.fetchall()]
        sois_text = "\n- ".join(sois) if sois else ""
        if sois_text:
            sois_text = "- " + sois_text
        logger.info(f"  {len(sois)} SOIs loaded")

        # 3. Requirements
        cur.execute("""
            SELECT DESCRIPTION FROM REQUEST_REQUIREMENTS
            WHERE REQUEST_ID = %s AND _FIVETRAN_DELETED = FALSE
            ORDER BY ID
        """, (request_id,))
        requirements = [row[0] for row in cur.fetchall()]
        requirements_text = "\n- ".join(requirements) if requirements else ""
        if requirements_text:
            requirements_text = "- " + requirements_text
        logger.info(f"  {len(requirements)} requirements loaded")

        # 4. Try to get LOOKING_FOR and OUT_OF_SCOPE from RFP
        looking_for = ""
        out_of_scope = ""
        trl_range = ""

        requestable_id = request.get("REQUESTABLE_ID")
        requestable_type = request.get("REQUESTABLE_TYPE")

        if requestable_type == "Rfp" and requestable_id:
            try:
                cur.execute("""
                    SELECT LOOKING_FOR, OUT_OF_SCOPE, TRL_RANGE, PROBLEM, BACKGROUND
                    FROM REQUEST_FOR_PROPOSALS
                    WHERE ID = %s AND _FIVETRAN_DELETED = FALSE
                """, (requestable_id,))
                rfp_row = cur.fetchone()
                if rfp_row:
                    rfp_cols = [desc[0] for desc in cur.description]
                    rfp = dict(zip(rfp_cols, rfp_row))
                    looking_for = rfp.get("LOOKING_FOR", "") or ""
                    out_of_scope = rfp.get("OUT_OF_SCOPE", "") or ""
                    trl_range = rfp.get("TRL_RANGE", "") or ""
                    logger.info(f"  RFP {requestable_id} loaded (looking_for, out_of_scope, trl_range)")
                else:
                    logger.info(f"  RFP {requestable_id} not found — synthesizing from SOIs/requirements")
            except Exception as e:
                logger.warning(f"  RFP lookup failed: {e}")

        # Synthesize LOOKING_FOR from SOIs if not available from RFP
        if not looking_for and sois:
            looking_for = (
                f"Looking for innovators in: {'; '.join(sois[:3])}"
            )

        return {
            "TITLE": request.get("TITLE", ""),
            "LOOKING_FOR": looking_for,
            "USE_CASE": request.get("USE_CASE", ""),
            "SOLUTIONS_OF_INTEREST": sois_text,
            "PARTNER_TYPES": partner_types,
            "TRL_RANGE": trl_range,
            "REQUIREMENTS": requirements_text,
            "OUT_OF_SCOPE": out_of_scope,
            "SLUG": request.get("SLUG", ""),
            # Raw data for reference
            "_sois_list": sois,
            "_requirements_list": requirements,
        }

    finally:
        cur.close()
        conn.close()


def get_request_company(request_id: int) -> Dict[str, Any]:
    """
    Load the requesting company for a request_id. Used to screen out
    leads from the company that posted the request — we don't reach
    out to them.

    Returns {"company_id", "company_name", "domains": [list]}.
    Returns {} if request has no company or lookup fails.
    """
    conn = get_snowflake_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COMPANY_ID FROM REQUESTS WHERE ID = %s", (request_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return {}
        company_id = row[0]

        cur.execute(
            "SELECT COMPANY_NAME, DOMAIN, EMAIL_DOMAIN FROM COMPANIES WHERE ID = %s",
            (company_id,),
        )
        c_row = cur.fetchone()
        name = (c_row[0] or "").strip() if c_row else ""
        domains = set()
        if c_row:
            for d in (c_row[1], c_row[2]):
                if d and d.strip():
                    domains.add(d.strip().lower())

        cur.execute(
            "SELECT DOMAIN FROM COMPANY_DOMAINS WHERE COMPANY_ID = %s",
            (company_id,),
        )
        for (d,) in cur.fetchall():
            if d and d.strip():
                domains.add(d.strip().lower())

        return {
            "company_id": company_id,
            "company_name": name,
            "domains": sorted(domains),
        }
    except Exception as e:
        logger.warning(f"get_request_company({request_id}) failed: {e}")
        return {}
    finally:
        cur.close()
        conn.close()


def find_halo_users_by_domain(domain: str) -> List[Dict[str, Any]]:
    """
    Find Halo users whose email matches a company domain.

    This is an early-stage lookup: when we discover a company (e.g. via Exa)
    but don't have specific emails yet, we check if anyone from that domain
    is already on Halo. Returns real users (non-shadow profiles).

    Args:
        domain: Email domain to search, e.g. "wageningen.nl" or "buhlergroup.com"

    Returns:
        List of dicts with id, first_name, last_name, email, role, verified, created_at
    """
    if not domain or not domain.strip():
        return []

    domain = domain.strip().lower()

    conn = get_snowflake_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT ID, FIRST_NAME, LAST_NAME, EMAIL, ROLE, VERIFIED, CREATED_AT
            FROM USERS
            WHERE LOWER(EMAIL) LIKE %s
              AND ROLE = 0
              AND IS_SHADOW_PROFILE = FALSE
              AND _FIVETRAN_DELETED = FALSE
            ORDER BY CREATED_AT DESC
            LIMIT 500
        """, (f"%@{domain}",))

        columns = [desc[0] for desc in cur.description]
        users = [dict(zip(columns, row)) for row in cur.fetchall()]

        logger.info(f"Domain lookup '{domain}': {len(users)} real users found")
        return users

    finally:
        cur.close()
        conn.close()


def find_halo_shadow_profiles_by_domain(domain: str) -> List[Dict[str, Any]]:
    """
    Find shadow profiles on Halo for a given email domain, with delivery info.

    Shadow profiles (is_shadow_profile=TRUE, role=0) are created when someone
    is mentioned/invited but hasn't signed up. Includes last notification
    delivery and bounce status to assess reachability.

    Args:
        domain: Email domain to search, e.g. "wageningen.nl"

    Returns:
        List of dicts with user info + cleaned_email + last_delivered_at + last_bounce_type
    """
    if not domain or not domain.strip():
        return []

    domain = domain.strip().lower()

    conn = get_snowflake_connection()
    cur = conn.cursor()

    try:
        # Query shadow profiles with latest delivery/bounce info
        # Uses subqueries instead of LATERAL (more portable across Snowflake versions)
        cur.execute("""
            SELECT
                u.ID,
                u.FIRST_NAME,
                u.LAST_NAME,
                u.EMAIL,
                REPLACE(
                    REGEXP_REPLACE(u.EMAIL, '^_unclaimed\\\\.', ''),
                    '@_unclaimed-', '@'
                ) AS CLEANED_EMAIL,
                u.ROLE,
                u.VERIFIED,
                u.CREATED_AT,
                latest_notif.SENT_AT AS LAST_NOTIFICATION_SENT_AT,
                latest_pm.DELIVERED_AT AS LAST_DELIVERED_AT,
                latest_pm.BOUNCE_TYPE AS LAST_BOUNCE_TYPE
            FROM USERS u
            LEFT JOIN (
                SELECT n.USER_ID, n.ID AS NOTIF_ID, n.SENT_AT,
                       ROW_NUMBER() OVER (PARTITION BY n.USER_ID ORDER BY n.SENT_AT DESC NULLS LAST) AS rn
                FROM NOTIFICATIONS n
            ) latest_notif ON latest_notif.USER_ID = u.ID AND latest_notif.rn = 1
            LEFT JOIN (
                SELECT pr.NOTIFICATION_ID, pr.DELIVERED_AT, pr.BOUNCE_TYPE,
                       ROW_NUMBER() OVER (PARTITION BY pr.NOTIFICATION_ID ORDER BY COALESCE(pr.DELIVERED_AT, pr.CREATED_AT) DESC NULLS LAST) AS rn
                FROM POSTMARK_RECORDS pr
            ) latest_pm ON latest_pm.NOTIFICATION_ID = latest_notif.NOTIF_ID AND latest_pm.rn = 1
            WHERE LOWER(u.EMAIL) LIKE %s
              AND u.ROLE = 0
              AND u.IS_SHADOW_PROFILE = TRUE
              AND u._FIVETRAN_DELETED = FALSE
              AND latest_notif.SENT_AT IS NOT NULL
            ORDER BY u.CREATED_AT DESC
            LIMIT 500
        """, (f"%@{domain}%",))

        columns = [desc[0] for desc in cur.description]
        profiles = [dict(zip(columns, row)) for row in cur.fetchall()]

        logger.info(
            f"Domain shadow lookup '{domain}': {len(profiles)} shadow profiles "
            f"({sum(1 for p in profiles if p.get('LAST_BOUNCE_TYPE') is None)} deliverable)"
        )
        return profiles

    finally:
        cur.close()
        conn.close()


def find_halo_users_by_domains(domains: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Batch domain lookup — checks multiple company domains at once.

    Useful during discovery when Exa returns multiple companies.
    Returns {domain: [users]} for domains that have matches.
    """
    if not domains:
        return {}

    results = {}
    for domain in domains:
        domain = domain.strip().lower()
        if not domain:
            continue

        users = find_halo_users_by_domain(domain)
        if users:
            results[domain] = users

    return results


def check_emails_on_halo(emails: List[str]) -> Dict[str, bool]:
    """
    Batch-check a list of emails against the Snowflake USERS table.

    Returns a dict mapping email → True/False for whether they exist on Halo.
    Only checks non-empty emails. Case-insensitive matching.
    """
    if not emails:
        return {}

    # Filter out empty/None emails
    clean_emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not clean_emails:
        return {}

    conn = get_snowflake_connection()
    cur = conn.cursor()

    try:
        # Batch query — Snowflake handles IN clauses well up to ~1000 values
        # For larger sets, chunk into batches
        found_emails = set()
        batch_size = 500

        for i in range(0, len(clean_emails), batch_size):
            batch = clean_emails[i:i + batch_size]
            placeholders = ", ".join(["%s"] * len(batch))
            cur.execute(f"""
                SELECT DISTINCT LOWER(EMAIL) AS EMAIL
                FROM USERS
                WHERE LOWER(EMAIL) IN ({placeholders})
                  AND _FIVETRAN_DELETED = FALSE
            """, batch)
            found_emails.update(row[0] for row in cur.fetchall())

        logger.info(f"Halo email check: {len(found_emails)}/{len(clean_emails)} found")

        return {email: email.lower() in found_emails for email in clean_emails}

    finally:
        cur.close()
        conn.close()


# =============================================================================
# CLI test
# =============================================================================
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    request_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1582
    data = get_request_data(request_id)

    print(f"\n{'='*60}")
    print(f"Request {request_id}: {data['TITLE']}")
    print(f"{'='*60}")
    for key in ["LOOKING_FOR", "USE_CASE", "SOLUTIONS_OF_INTEREST",
                 "PARTNER_TYPES", "TRL_RANGE", "REQUIREMENTS", "OUT_OF_SCOPE"]:
        val = data.get(key, "")
        if val:
            print(f"\n--- {key} ---")
            print(val)
