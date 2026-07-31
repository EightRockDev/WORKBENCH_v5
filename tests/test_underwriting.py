

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
