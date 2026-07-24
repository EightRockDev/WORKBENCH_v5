"""Effective permissions & server-side field masking (spec Section 10.4).

Presets compose from four primitives (Section 10.4):
  * **module grants** - which modules the preset may open
  * **field masks**   - sensitive fields stripped server-side before display
  * **action grants** - gated verbs (commit_go_nogo, edit_waterfall, ...)
  * **scope**         - row visibility (org_all | portfolio:[..] | deal:[..] |
                        own_only | single_deal:id)

The key guarantee (the "three answers", 10.4): a masked field never leaves the
server. A Maintenance preset's browser never receives the purchase price because
:meth:`Permissions.mask` replaces it here, before it is ever rendered — this is
enforcement by construction, not by hiding a UI element.
"""

from __future__ import annotations

from dataclasses import dataclass

# Canonical field-mask keys (Section 10.4).
FIELD_PURCHASE_PRICE = "purchase_price"
FIELD_RETURNS_IRR = "returns_irr"
FIELD_WATERFALL_PROMOTE = "waterfall_promote"
FIELD_LP_PII = "lp_pii"
FIELD_DEBT_TERMS = "debt_terms"

MASK_PLACEHOLDER = "•••"


@dataclass(frozen=True)
class Permissions:
    """A user's effective permissions within one organization."""

    org_id: str
    role_preset: str
    modules: frozenset[str]
    masks: frozenset[str]
    actions: frozenset[str]
    scope: str

    # --- module access ---
    def can_open(self, module: str) -> bool:
        return module in self.modules

    # --- action gating (server-side, every request) ---
    def can(self, action: str) -> bool:
        return action in self.actions

    def require(self, action: str) -> None:
        """Raise PermissionError unless the action is granted (403 semantics)."""
        if action not in self.actions:
            raise PermissionError(f"action '{action}' not permitted for role '{self.role_preset}'")

    # --- field masking ---
    def is_masked(self, field: str) -> bool:
        return field in self.masks

    def mask(self, field: str, value, placeholder: str = MASK_PLACEHOLDER):
        """Return the value, or the placeholder if this field is masked.

        Call this on every sensitive value before it is rendered/serialized so a
        masked field never reaches the client (Section 10.4).
        """
        return placeholder if field in self.masks else value

    def scrub(self, record: dict) -> dict:
        """Return a copy of ``record`` with masked keys replaced. Use when
        serializing a whole row to a browser/API response."""
        return {k: (MASK_PLACEHOLDER if k in self.masks else v) for k, v in record.items()}

    # --- scope (row visibility) ---
    @property
    def scope_kind(self) -> str:
        """'org_all' | 'portfolio' | 'deal' | 'own_only' | 'single_deal'."""
        return self.scope.split(":", 1)[0]

    @property
    def scope_ids(self) -> list[str]:
        """The id list/value for a scoped preset (empty for org_all/own_only)."""
        if ":" not in self.scope:
            return []
        raw = self.scope.split(":", 1)[1].strip()
        raw = raw.strip("[]")
        return [s.strip().strip("'\"") for s in raw.split(",") if s.strip()]

    @classmethod
    def from_preset_row(cls, org_id: str, preset: dict, scope: str | None = None) -> "Permissions":
        """Build from a role_presets row (dict) + an optional membership scope
        override (falls back to the preset's default_scope)."""
        return cls(
            org_id=org_id,
            role_preset=preset["key"],
            modules=frozenset(preset.get("module_grants") or ()),
            masks=frozenset(preset.get("field_mask") or ()),
            actions=frozenset(preset.get("action_grants") or ()),
            scope=scope or preset.get("default_scope") or "org_all",
        )
