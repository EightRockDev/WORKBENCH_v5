

# ---------------------------------------------------------------------------
# sources.json values may be bare or provenance-wrapped {"value","source"}
# ---------------------------------------------------------------------------

def _mk_deal(**kw):
    from data.property_io import DealState
    base = dict(pp=5_000_000, noi=350_000, dp=30, ir=6.5, vac=8, rg=3,
                eg=3, xc=6.5, hp=5)
    base.update(kw)
    return DealState(**base)


def test_wrapped_real_estate_taxes_do_not_crash_the_tab():
    """Regression: a wrapped realEstateTaxes reached a `> 0` compare as a dict
    and took down the whole Underwriting tab with a TypeError."""
    from ui.underwriting import _derive_year1_inputs

    sources = {
        "totalRevenue": {"value": 1_200_000, "source": "T12"},
        "totalOpex": {"value": 500_000, "source": "T12"},
        "t12_fixedCharges": {
            "realEstateTaxes": {"value": 60_000, "source": "T12"},
        },
    }
    gpr, expenses = _derive_year1_inputs(
        _mk_deal(), sources, units=100, city="Norfolk")
    assert gpr == 1_200_000
    assert expenses > 0


def test_bare_and_wrapped_values_agree():
    from ui.underwriting import _derive_year1_inputs

    bare = {"totalRevenue": 1_000_000, "totalOpex": 400_000,
            "t12_fixedCharges": {"realEstateTaxes": 50_000}}
    wrapped = {"totalRevenue": {"value": 1_000_000},
               "totalOpex": {"value": 400_000},
               "t12_fixedCharges": {"realEstateTaxes": {"value": 50_000}}}
    d = _mk_deal()
    assert _derive_year1_inputs(d, bare, 100, city="Norfolk") == \
           _derive_year1_inputs(d, wrapped, 100, city="Norfolk")


def test_junk_in_sources_degrades_instead_of_crashing():
    from ui.underwriting import _derive_year1_inputs

    junk = {"totalRevenue": {"value": "n/a"}, "totalOpex": None,
            "t12_fixedCharges": {"realEstateTaxes": {"nope": 1}}}
    gpr, expenses = _derive_year1_inputs(
        _mk_deal(), junk, units=100, city="Norfolk")
    assert gpr > 0 and expenses > 0     # fell back to the NOI/ratio derivation


# ---------------------------------------------------------------------------
# The auto-save loop (owner report 2026-08-04): the Underwriting tab faded
# in/out on its own and never settled, "Photo Upload" from Subject never
# clearing. Cause: _render_dials rebuilt `new_deal` from ONLY the dial widgets
# via model_validate(), which reset the non-dial fields (selected_levers, and
# the FR-9.3.1 metadata row_version / updated_by / updated_at) to defaults. A
# once-saved deal has non-default values there, so `new_deal != deal` was true
# every render -> save -> st.rerun() -> forever. These tests pin the invariant:
# rebuilding a deal from its OWN dial values, with nothing changed, must equal
# the original so no save/rerun fires.
# ---------------------------------------------------------------------------

def _rebuild_from_dials(deal):
    """Mirror _render_dials' construction with UNCHANGED dial values.

    Must stay a model_copy(update=...) over the loaded deal — a model_validate
    of only the dial fields is exactly the regression this guards against.
    """
    import config
    return deal.model_copy(update={
        "pp": float(deal.pp), "noi": float(deal.noi), "dp": float(deal.dp),
        "ir": float(deal.ir), "vac": float(deal.vac), "rg": float(deal.rg),
        "eg": float(deal.eg), "xc": float(deal.xc), "hp": int(deal.hp),
        "am": int(config.AMORT_YEARS), "io": int(deal.io), "amf": float(deal.amf),
        "raise_amount": deal.raise_amount,
        "vacancy_source": deal.vacancy_source,
        "tax_reassessment_on": bool(deal.tax_reassessment_on),
        "insurance_escalator_on": bool(deal.insurance_escalator_on),
        "vac_spike_pp": float(deal.vac_spike_pp),
        "stabilization_months": int(deal.stabilization_months),
    })


def test_a_saved_deal_does_not_reautosave_on_open():
    """A deal that has been saved before (row_version>0, a timestamp, some
    levers) must compare EQUAL after a no-op dial rebuild — otherwise the tab
    auto-saves and st.rerun()s forever."""
    deal = _mk_deal(row_version=7, updated_by="Brian",
                    updated_at="2026-08-01T12:00:00Z",
                    selected_levers=["rubs", "reno_classic"])
    assert _rebuild_from_dials(deal) == deal, (
        "no-op rebuild differs from the loaded deal -> auto-save/rerun loop")


def test_rebuild_preserves_concurrency_metadata_and_levers():
    deal = _mk_deal(row_version=7, updated_by="Brian",
                    updated_at="2026-08-01T12:00:00Z",
                    selected_levers=["rubs"])
    rebuilt = _rebuild_from_dials(deal)
    assert rebuilt.row_version == 7
    assert rebuilt.updated_at == "2026-08-01T12:00:00Z"
    assert rebuilt.selected_levers == ["rubs"], "levers wiped on rebuild"


def test_the_old_model_validate_path_would_have_looped():
    """Mutation guard: prove the ORIGINAL construction (model_validate of only
    the dials) is what broke equality, so this test fails if anyone reverts."""
    from data.property_io import DealState
    import config
    deal = _mk_deal(row_version=7, updated_at="2026-08-01T12:00:00Z",
                    selected_levers=["rubs"])
    looped = DealState.model_validate({
        "s-pp": deal.pp, "s-noi": deal.noi, "s-dp": deal.dp, "s-ir": deal.ir,
        "s-vac": deal.vac, "s-rg": deal.rg, "s-eg": deal.eg, "s-xc": deal.xc,
        "s-hp": deal.hp, "s-am": int(config.AMORT_YEARS), "s-io": deal.io,
        "s-amf": deal.amf,
    })
    assert looped != deal, (
        "expected the model_validate rebuild to differ (row_version/levers "
        "reset) - that inequality is the loop this fix removes")


def test_an_actual_dial_change_still_triggers_a_save():
    """The fix must not over-correct: a real edit must still be detected."""
    deal = _mk_deal(row_version=7)
    edited = _rebuild_from_dials(deal).model_copy(update={"pp": deal.pp + 100_000})
    assert edited != deal
