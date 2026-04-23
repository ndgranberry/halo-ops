#!/usr/bin/env python3
"""
Agent Scout — Domain Resolver
===============================
Resolves company/institution names to their website domains.
Uses a multi-tier strategy:
  1. Known domains cache (instant, free)
  2. Exa company search (accurate, costs API credits)
  3. Heuristic fallback (fast, free, less accurate)
"""

import logging
import os
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Domains to skip when parsing Exa results
SKIP_DOMAINS = {
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "crunchbase.com", "bloomberg.com", "google.com", "youtube.com",
    "wikidata.org", "glassdoor.com", "indeed.com", "zoominfo.com",
    "pitchbook.com", "dnb.com", "owler.com", "yelp.com", "bbb.org",
    "reddit.com", "quora.com", "medium.com",
}

# Generic email providers to skip when extracting domain from email
GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "126.com", "163.com", "qq.com", "icloud.com", "mail.ru",
    "protonmail.com", "aol.com", "yandex.com", "live.com",
    "me.com", "msn.com", "zoho.com",
}

# Well-known company/institution → domain mappings
KNOWN_DOMAINS: Dict[str, str] = {
    # Automotive OEMs
    "toyota": "toyota.com", "toyota motor": "toyota.com",
    "ford": "ford.com", "ford motor": "ford.com", "ford motor company": "ford.com",
    "general motors": "gm.com", "gm": "gm.com",
    "volkswagen": "volkswagen.de", "bmw": "bmw.com", "bmw group": "bmw.com",
    "daimler": "daimler.com", "mercedes-benz": "mercedes-benz.com",
    "honda": "honda.com", "hyundai": "hyundai.com", "hyundai motor company": "hyundai.com",
    "stellantis": "stellantis.com", "nissan": "nissan-global.com",
    "tesla": "tesla.com", "rivian": "rivian.com", "lucid motors": "lucidmotors.com",
    # Coatings & chemicals
    "ppg": "ppg.com", "ppg industries": "ppg.com", "ppg industries france": "ppg.com",
    "axalta": "axalta.com", "axalta coating systems": "axalta.com",
    "basf": "basf.com", "basf coatings": "basf.com",
    "akzo nobel": "akzonobel.com", "akzonobel": "akzonobel.com",
    "sherwin-williams": "sherwin-williams.com", "sherwin williams": "sherwin-williams.com",
    "the sherwin-williams company": "sherwin-williams.com",
    "dupont": "dupont.com", "nippon paint": "nipponpaint.com",
    "kansai paint": "kansaipaint.co.jp",
    "3m": "3m.com", "3m company": "3m.com",
    "dow": "dow.com", "dow chemical": "dow.com", "the dow chemical company": "dow.com",
    "evonik": "evonik.com", "evonik industries": "evonik.com",
    "henkel": "henkel.com", "covestro": "covestro.com",
    "clariant": "clariant.com", "solvay": "solvay.com",
    "arkema": "arkema.com", "eastman": "eastman.com", "eastman chemical": "eastman.com",
    "momentive": "momentive.com", "allnex": "allnex.com",
    # Pigments & effects
    "schlenk": "schlenk.com", "schlenk metallic pigments": "schlenk.com",
    "silberline": "silberline.com", "sun chemical": "sunchemical.com",
    "altana": "altana.com", "eckart": "eckart.net", "eckart america": "eckart.net",
    "byk": "byk.com", "merck": "merck.com", "merck kgaa": "emdgroup.com",
    "sudarshan": "sudchem.com", "sudarshan chemical": "sudchem.com",
    "toyal": "toyal.co.jp",
    # Equipment
    "elcometer": "elcometer.com", "elcometer spray equipment": "elcometer.com",
    # Research institutes
    "csic": "csic.es", "cnrs": "cnrs.fr", "fraunhofer": "fraunhofer.de",
    "max planck": "mpg.de", "nist": "nist.gov",
    # Major universities
    "mit": "mit.edu", "stanford": "stanford.edu", "stanford university": "stanford.edu",
    "harvard": "harvard.edu", "harvard university": "harvard.edu",
    "oxford": "ox.ac.uk", "university of oxford": "ox.ac.uk",
    "cambridge": "cam.ac.uk", "university of cambridge": "cam.ac.uk",
    "eth zurich": "ethz.ch", "epfl": "epfl.ch",
    "aalto university": "aalto.fi",
    "tu berlin": "tu.berlin", "technische universität berlin": "tu.berlin",
    "tu dortmund": "tu-dortmund.de", "tu dortmund university": "tu-dortmund.de",
    "technical university of denmark": "dtu.dk", "dtu": "dtu.dk",
    "university of leeds": "leeds.ac.uk",
    "zhejiang university": "zju.edu.cn",
    "sogang university": "sogang.ac.kr",
    "jeonbuk national university": "jbnu.ac.kr",
    "hohai university": "hhu.edu.cn",
    "southwest university": "swu.edu.cn",
    "ku leuven": "kuleuven.be",
    "universidad de alicante": "ua.es",
    "universitat politècnica de catalunya": "upc.edu",
    "polytechnic university of catalonia": "upc.edu",
    "national polytechnic institute": "ipn.mx",
    "toyota central research and development laboratories": "tytlabs.co.jp",
}


class DomainResolver:
    """Resolve company/institution names to website domains."""

    def __init__(self, exa_api_key: Optional[str] = None, use_exa: bool = True):
        self.exa_api_key = exa_api_key or os.getenv("EXA_API_KEY")
        self.use_exa = use_exa and bool(self.exa_api_key)
        # Runtime cache to avoid duplicate Exa lookups
        self._cache: Dict[str, str] = {}

    def resolve(
        self,
        company_name: str,
        email: str = "",
        company_description: str = "",
    ) -> str:
        """
        Resolve a company/institution name to its domain.

        Priority:
          1. Email domain (if not generic)
          2. URL in company_description
          3. Known domains cache
          4. Runtime cache (previous Exa lookups)
          5. Exa company search
          6. Heuristic fallback
        """
        # 1. From email
        if email and "@" in email:
            domain = email.split("@")[1].lower().strip()
            if domain not in GENERIC_EMAIL_DOMAINS:
                return domain

        # 2. From company_description URL
        if company_description:
            url_match = re.search(
                r'https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-z]{2,})',
                company_description,
            )
            if url_match:
                return url_match.group(1).lower()

        if not company_name:
            return ""

        # 3. Known domains
        lookup_key = company_name.lower().strip()
        if lookup_key in KNOWN_DOMAINS:
            return KNOWN_DOMAINS[lookup_key]

        # 4. Runtime cache
        if lookup_key in self._cache:
            return self._cache[lookup_key]

        # 5. Exa company search
        if self.use_exa:
            domain = self._exa_resolve(company_name)
            if domain:
                self._cache[lookup_key] = domain
                return domain

        # 6. Heuristic fallback
        domain = self._heuristic_domain(company_name)
        self._cache[lookup_key] = domain
        return domain

    def _exa_resolve(self, company_name: str) -> str:
        """Use Exa company search to find the domain."""
        try:
            resp = requests.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": self.exa_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "query": company_name,
                    "type": "auto",
                    "category": "company",
                    "numResults": 3,
                },
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])

            for r in results:
                url = r.get("url", "")
                if not url:
                    continue
                parsed = urlparse(url)
                domain = parsed.netloc.replace("www.", "")
                # Skip aggregator/social sites
                base = ".".join(domain.split(".")[-2:])
                if base in SKIP_DOMAINS or domain in SKIP_DOMAINS:
                    continue
                # Strip subdomain for common patterns (e.g. en.hhu.edu.cn → hhu.edu.cn)
                # Keep subdomains for short TLDs like .com, .net, .org
                # but strip for multi-part TLDs like .edu.cn, .ac.uk, .co.jp
                domain = self._clean_subdomain(domain)
                logger.debug(f"Exa resolved '{company_name}' → {domain}")
                return domain

        except Exception as e:
            logger.warning(f"Exa domain lookup failed for '{company_name}': {e}")

        return ""

    @staticmethod
    def _clean_subdomain(domain: str) -> str:
        """Remove unnecessary subdomains while keeping the meaningful part.

        e.g. en.hhu.edu.cn → hhu.edu.cn, uafg.ua.es → ua.es,
        but keep: app.nanomorphix.com → nanomorphix.com
        """
        parts = domain.split(".")

        # For short domains (2-3 parts), keep as-is
        if len(parts) <= 3:
            # e.g. aalto.fi, csic.es, ppg.com, tu.berlin
            # But strip common subdomains
            if parts[0] in ("en", "www", "m", "app", "api", "mail"):
                return ".".join(parts[1:])
            return domain

        # For 4+ parts, try to find the "root" domain
        # Patterns: xx.edu.cn, xx.ac.uk, xx.co.jp, xx.ac.kr, xx.ac.jp
        multi_part_tlds = {
            ("edu", "cn"), ("ac", "uk"), ("co", "jp"), ("ac", "jp"),
            ("ac", "kr"), ("co", "kr"), ("ac", "in"), ("co", "in"),
            ("com", "au"), ("edu", "au"), ("co", "nz"), ("ac", "nz"),
            ("co", "za"), ("ac", "za"), ("com", "br"), ("edu", "br"),
        }
        if len(parts) >= 4 and (parts[-2], parts[-1]) in multi_part_tlds:
            # Keep: name.edu.cn, strip prefix subdomains
            return ".".join(parts[-3:])

        # Default: keep last 3 parts (strip deep subdomains)
        if len(parts) >= 4 and parts[0] in ("en", "www", "m", "app", "api", "mail", "fr", "de", "es"):
            return ".".join(parts[1:])

        return domain

    @staticmethod
    def _heuristic_domain(company_name: str) -> str:
        """Best-effort domain guess from company name."""
        name = company_name.lower().strip()

        # Strip common suffixes
        for suffix in [
            " inc", " inc.", " corp", " corp.", " corporation",
            " ltd", " ltd.", " limited", " llc", " gmbh", " ag",
            " sa", " s.a.", " bv", " b.v.", " co.", " plc",
            " pty", " srl", " s.r.l.", " oy", " se",
            " sp. z o.o.", " sp.z o.o.", " pvt", " private limited",
        ]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        name = name.strip()

        # For short names (1-2 words), use directly
        words = name.split()
        if len(words) <= 2:
            cleaned = re.sub(r"[^a-z0-9-]", "", name.replace(" ", ""))
            if cleaned and len(cleaned) >= 2:
                return f"{cleaned}.com"

        # For longer names, try first word if distinctive (4+ chars)
        if len(words) >= 2 and len(words[0]) >= 4:
            first_word = re.sub(r"[^a-z0-9]", "", words[0])
            if first_word and len(first_word) >= 4:
                return f"{first_word}.com"

        # Fallback: concatenate
        cleaned = re.sub(r"[^a-z0-9]", "", name.replace(" ", ""))
        if cleaned and len(cleaned) >= 3:
            return f"{cleaned}.com"

        return ""

    def resolve_batch(
        self,
        items: list,
        company_field: str = "company",
        email_field: str = "email",
        description_field: str = "company_description",
    ) -> Dict[str, str]:
        """Resolve domains for a batch of items. Returns {company_name: domain}."""
        results = {}
        for item in items:
            company = item.get(company_field, "") if isinstance(item, dict) else getattr(item, company_field, "")
            if not company or company in results:
                continue
            email = item.get(email_field, "") if isinstance(item, dict) else getattr(item, email_field, "")
            desc = item.get(description_field, "") if isinstance(item, dict) else getattr(item, description_field, "")
            results[company] = self.resolve(company, email=email, company_description=desc)
        return results
