"""
shared/bot_config.py — Canonical config schema for every Frank.Bot instance.

Every sold bot gets a config.json written at provisioning time.
This module loads, validates, and provides defaults for that config.

Config lives at: /opt/frankbot/config.json (on client droplet)
                 state/bots/<bot_id>/config.json (on LevelUp HQ)
"""
import os, json
from pathlib import Path
from typing import Optional

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULTS = {
    # Identity
    "bot_name": "Frank",
    "company_name": "Your Company",
    "company_description": "",
    "vertical": "hr_general",          # key from vertical_prompts.py
    "bot_id": "",                       # unique slug e.g. "intercon-abc123"

    # LLM
    "llm_provider": "anthropic",
    "llm_model": "claude-haiku-4-5",
    "llm_api_key": "",                  # client's own Anthropic key

    # RAG
    "rag_enabled": True,
    "rag_persist_dir": "/opt/frankbot/chroma",
    "rag_top_k": 15,
    "rag_min_relevance": 0.25,

    # Docs
    "docs_dir": "/opt/frankbot/docs",
    "max_docs": 50,
    "max_doc_size_mb": 20,

    # Features
    "forms_enabled": True,
    "form_schemas": [],                 # list of form schema names to load
    "channels": ["webchat"],           # webchat | teams | slack | email

    # Scope
    # audience: who is this bot for?
    #   "internal"  — staff/employees only (HR bots, policy assistants, etc.)
    #   "customer"  — customers, prospects, and partners
    #   "public"    — anyone (no restriction)
    #   "mixed"     — both internal and external audiences
    # Defaults to "public" — never assume staff-only unless explicitly set.
    "audience": "public",
    # Legacy flag — kept for backwards compatibility, derived from audience on read.
    # Do NOT set this to True on new bots; set audience="internal" instead.
    "scope_internal_only": False,
    "custom_instructions": "",          # client-specific additions to system prompt

    # Tier
    "tier": "starter",                  # starter | professional | enterprise
    "tier_price": 0,

    # Add-ons
    "form_limit": 0,                    # total forms allowed (tier base + pack add-ons)
    "forms_active": [],                 # template IDs currently activated/in-use
    "form_pack": None,                  # purchased pack key e.g. "form_pack_small"
    "vision_drawing": False,            # True = Drawing & Image Analysis add-on purchased
    "prompt_customisation": False,      # False | "lite" | "pro"
    "prompt_addons": [],                # list of active prompt addon block ids
    "hosting_managed": False,           # True if LevelUp manages the hosting
    "support_tier": None,               # None | "basic"

    # Integrations
    "integrations_enabled": True,       # inbound ingest + outbound webhooks

    # Teams Live add-on
    "teams_enabled": False,             # True when Teams Live purchased
    "teams_team_id": "",                # M365 group/team ID
    "teams_channel_ids": [],            # [] = all standard channels
    "teams_index_messages": True,       # index channel messages
    "teams_index_files": True,          # index SharePoint files
    "teams_index_interval_minutes": 60, # re-index frequency
    "teams_file_extensions": ["pdf","docx","pptx","txt","xlsx","csv"],

    # LevelUp HQ (for support ticket forwarding etc.)
    "hq_url": "http://your-hq-server:8990",

    # LevelUp HQ
    "hq_url": "http://your-hq-server:8990",
    "hq_token": "",

    # Channels — Messenger
    "messenger_page_access_token": "",  # Meta Page Access Token
    "messenger_verify_token": "",       # Arbitrary string set in Meta dashboard
    "messenger_page_id": "",            # Facebook Page ID

    # Ports
    "port": 8080,
    "admin_port": 8081,
}

TIER_LIMITS = {
    # form_limit = forms included in the base plan price
    # Additional forms via form pack add-ons increase form_limit on top of this
    "starter":      {"max_docs": 10,  "max_doc_size_mb": 5,  "forms_enabled": True, "form_limit": 1},
    "professional": {"max_docs": 30,  "max_doc_size_mb": 10, "forms_enabled": True, "form_limit": 3},
    "enterprise":   {"max_docs": 100, "max_doc_size_mb": 20, "forms_enabled": True, "form_limit": 5},
}

# Form pack add-ons map to additional form slots on top of tier base
FORM_PACK_LIMITS = {
    "form_pack_starter":   1,
    "form_pack_small":     3,
    "form_pack_medium":    5,
    "form_pack_large":     10,
    "form_pack_unlimited": 999,
}


class BotConfig:
    def __init__(self, data: dict):
        self._data = {**DEFAULTS, **data}
        # Apply tier limits (don't override explicitly-set values from purchase/config)
        tier = self._data.get("tier", "starter")
        limits = TIER_LIMITS.get(tier, TIER_LIMITS["starter"])
        for k, v in limits.items():
            if k not in data:  # don't override explicit overrides
                self._data[k] = v
        # form_limit: if explicitly set (e.g. from purchased pack), use it;
        # but it must be >= tier default (enterprise gets 3 free)
        explicit_form_limit = data.get("form_limit")
        tier_form_limit = limits.get("form_limit", 0)
        if explicit_form_limit is not None:
            self._data["form_limit"] = max(int(explicit_form_limit), tier_form_limit)
        else:
            self._data["form_limit"] = tier_form_limit
        # Add form pack slots on top of tier base
        pack_key = data.get("form_pack")
        if pack_key and pack_key in FORM_PACK_LIMITS:
            self._data["form_limit"] = self._data["form_limit"] + FORM_PACK_LIMITS[pack_key]

        # forms_enabled follows form_limit
        if self._data["form_limit"] > 0:
            self._data["forms_enabled"] = True

        # Ensure forms_active is a list
        if not isinstance(self._data.get("forms_active"), list):
            self._data["forms_active"] = []

        # prompt_customisation flag (from tier or add-on)
        if tier == "enterprise" and not data.get("prompt_customisation"):
            self._data["prompt_customisation"] = "pro"  # enterprise includes Prompt Pro

    # ── Form access helpers ───────────────────────────────────────────────────

    @property
    def forms_used(self) -> int:
        return len(self._data.get("forms_active", []))

    @property
    def forms_remaining(self) -> int:
        return max(0, self._data.get("form_limit", 0) - self.forms_used)

    def can_activate_form(self) -> bool:
        """True if the client has capacity to activate another template."""
        return self._data.get("forms_enabled", False) and self.forms_remaining > 0

    def activate_form(self, template_id: str, config_path: str = None) -> dict:
        """Activate a template. Returns {"ok": bool, "reason": str}."""
        active = self._data.setdefault("forms_active", [])
        if template_id in active:
            return {"ok": True, "reason": "already active"}
        if not self._data.get("forms_enabled", False):
            return {"ok": False, "reason": "Forms not enabled on this plan — upgrade to unlock"}
        if self.forms_remaining <= 0:
            limit = self._data.get("form_limit", 0)
            return {"ok": False, "reason": f"Form limit reached ({limit}/{limit}). Purchase a form pack to add more."}
        active.append(template_id)
        if config_path:
            self.save(config_path)
        return {"ok": True, "reason": f"Activated ({self.forms_used}/{self._data['form_limit']} slots used)"}

    def deactivate_form(self, template_id: str, config_path: str = None) -> dict:
        """Deactivate a template (frees up a slot)."""
        active = self._data.setdefault("forms_active", [])
        if template_id in active:
            active.remove(template_id)
            if config_path:
                self.save(config_path)
            return {"ok": True, "reason": f"Deactivated ({self.forms_used}/{self._data['form_limit']} slots used)"}
        return {"ok": False, "reason": "Template not active"}

    def forms_status(self) -> dict:
        """Return a summary for display in the admin panel."""
        return {
            "forms_enabled": self._data.get("forms_enabled", False),
            "form_limit":    self._data.get("form_limit", 0),
            "forms_used":    self.forms_used,
            "forms_remaining": self.forms_remaining,
            "forms_active":  self._data.get("forms_active", []),
            "tier":          self._data.get("tier", "starter"),
            "form_pack":     self._data.get("form_pack"),
        }

    @property
    def scope_internal_only(self) -> bool:
        """True only when audience is explicitly 'internal'.
        Legacy bots that set scope_internal_only=True in config.json are respected."""
        audience = self._data.get("audience", "public")
        if audience == "internal":
            return True
        # Legacy: explicit True in config.json still triggers internal-only
        return bool(self._data.get("scope_internal_only", False))

    def __getattr__(self, key):
        if key.startswith("_"):
            return super().__getattribute__(key)
        return self._data.get(key)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return dict(self._data)

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Never write LLM key to disk in plaintext in prod
        safe = {k: v for k, v in self._data.items() if k != "llm_api_key"}
        Path(path).write_text(json.dumps(safe, indent=2))

    @classmethod
    def load(cls, path: str) -> "BotConfig":
        data = json.loads(Path(path).read_text())
        # LLM key comes from env at runtime
        data.setdefault("llm_api_key", os.environ.get("LLM_API_KEY", ""))
        return cls(data)

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load config from environment variables (for containerised deploys)."""
        data = {
            "bot_id":                       os.environ.get("BOT_ID", ""),
            "bot_name":                     os.environ.get("BOT_NAME", ""),   # Fix: wire BOT_NAME env var
            "company_name":                 os.environ.get("COMPANY_NAME", ""),
            "vertical":                     os.environ.get("VERTICAL", "hr_general"),
            "llm_api_key":                  os.environ.get("LLM_API_KEY", ""),
            "llm_provider":                 os.environ.get("LLM_PROVIDER", "anthropic"),
            "tier":                         os.environ.get("TIER", "starter"),
            "hq_url":                       os.environ.get("HQ_URL", "http://your-hq-server:8990"),
            "hq_token":                     os.environ.get("HQ_TOKEN", ""),
            "port":                         int(os.environ.get("PORT", "8080")),
            "rag_persist_dir":              os.environ.get("RAG_DIR", "/opt/frankbot/chroma"),
            "docs_dir":                     os.environ.get("DOCS_DIR", "/opt/frankbot/docs"),
            "messenger_page_access_token":  os.environ.get("MESSENGER_PAGE_ACCESS_TOKEN", ""),
            "messenger_verify_token":       os.environ.get("MESSENGER_VERIFY_TOKEN", ""),
            "messenger_page_id":            os.environ.get("MESSENGER_PAGE_ID", ""),
        }
        # Load root config.json (written by provisioner at install time)
        config_path = os.environ.get("CONFIG_PATH", "/opt/frankbot/config.json")
        if Path(config_path).exists():
            file_data = json.loads(Path(config_path).read_text())
            # Only let env vars override config.json if they were EXPLICITLY set in the environment
            # (not just default fallback values) — prevents "starter" default clobbering "professional"
            explicitly_set = {
                k: v for k, v in data.items()
                if v and os.environ.get(k.upper())  # key must actually exist in environment
            }
            file_data.update(explicitly_set)
            data = file_data

        # Merge wizard config (app/config.json) on top — wizard settings always win.
        # This is written by setup_wizard when client completes onboarding.
        wizard_path = Path(config_path).parent / "app" / "config.json"
        if wizard_path.exists():
            try:
                wizard_data = json.loads(wizard_path.read_text())
                # Wizard fields that should override root config
                WIZARD_FIELDS = {
                    "bot_name", "company_name", "vertical", "custom_instructions",
                    "tone", "activated", "system_prompt_override", "audience",
                    "teams_tenant_id", "teams_client_id", "teams_client_secret",
                    "teams_tenant_id_pending", "teams_client_id_pending",
                    "teams_client_secret_pending", "teams_workspace_name_pending",
                    "teams_enabled", "teams_live_purchased", "teams_index_files",
                    "prompt_customisation", "prompt_addons",
                }
                for field in WIZARD_FIELDS:
                    if field in wizard_data and wizard_data[field] not in (None, "", [], {}):
                        data[field] = wizard_data[field]
            except Exception as e:
                print(f"[BotConfig] Warning: could not merge wizard config: {e}", flush=True)

        return cls(data)


def make_config(order: dict) -> BotConfig:
    """Build a BotConfig from a checkout order dict."""
    return BotConfig({
        "bot_id":           order.get("bot_id", ""),
        "company_name":     order.get("company_name", ""),
        "vertical":         order.get("vertical", "hr_general"),
        "tier":             order.get("tier", "starter"),
        "tier_price":       order.get("tier_price", 0),
        "channels":         order.get("channels", ["webchat"]),
        "form_schemas":     order.get("forms", []),
        "custom_instructions": order.get("custom_instructions", ""),
        "hq_token":         order.get("hq_token", ""),
    })
