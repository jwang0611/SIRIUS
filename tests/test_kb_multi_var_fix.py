"""Verify KB results: pipe-patterns preserved, QVAL-when decomposed."""

from src.processors.postprocess import PostprocessMixin


class FakeHost(PostprocessMixin):
    def __init__(self):
        self.debug = True
        self.domain_override_threshold = 0.95
        self._deterministic_validator = None

    def _infer_testcd(self, *a, **kw):
        return ""


def test_kb_pipe_pattern_preserved():
    """KB multi-variable patterns with | must be preserved intact."""
    host = FakeHost()
    kb_recs = [
        {
            "domain": "EC|EX",
            "sdtm_variable": "ECSTDTC when ECMOOD=\u5df2\u6267\u884c|EXSTDTC",
            "sdtm_variable_type": "standard",
            "score": 1.0,
            "source": "KB",
            "kb_validated": True,
            "variable_name": "EXSTTIM",
        }
    ]
    result = host._normalize_domain_recs("EX", "EXSTTIM", kb_recs, target_domain="EC", enforce_domain=True)
    assert len(result) == 1
    assert result[0]["sdtm_variable"] == "ECSTDTC when ECMOOD=\u5df2\u6267\u884c|EXSTDTC"
    assert result[0]["domain"] == "EC|EX"
    assert result[0]["score"] == 1.0
    print(f"PASS: pipe pattern preserved: {result[0]['sdtm_variable']}")


def test_kb_qval_when_qnam_decomposed():
    """KB SUPP patterns 'QVAL when QNAM=xxx' must be decomposed."""
    host = FakeHost()
    kb_recs = [
        {
            "domain": "EC",
            "sdtm_variable": "QVAL when QNAM=EXREAS",
            "sdtm_variable_type": "standard",
            "score": 1.0,
            "source": "KB",
            "kb_validated": True,
            "variable_name": "EXREAS",
        }
    ]
    result = host._normalize_domain_recs("EX", "EXREAS", kb_recs, target_domain="EC", enforce_domain=True)
    assert len(result) == 1
    assert result[0]["sdtm_variable"] == "QVAL"
    assert result[0]["supp_variable"] == "EXREAS"
    assert result[0]["score"] == 1.0
    print(f"PASS: QVAL when decomposed: sdtm_var={result[0]['sdtm_variable']}, supp_var={result[0]['supp_variable']}")


def test_kb_ecoccur_pipe_pattern():
    """KB pattern like 'ECOCCUR when ECTRT=QL1706|EXTRT' must be preserved."""
    host = FakeHost()
    kb_recs = [
        {
            "domain": "EC|EX",
            "sdtm_variable": "ECOCCUR when ECTRT=QL1706|EXTRT",
            "sdtm_variable_type": "standard",
            "score": 1.0,
            "source": "KB",
            "kb_validated": True,
            "variable_name": "EXYN",
        }
    ]
    result = host._normalize_domain_recs("EX", "EXYN", kb_recs, target_domain="EC", enforce_domain=True)
    assert len(result) == 1
    assert result[0]["sdtm_variable"] == "ECOCCUR when ECTRT=QL1706|EXTRT"
    assert result[0]["score"] == 1.0
    print(f"PASS: pipe pattern preserved: {result[0]['sdtm_variable']}")


def test_llm_when_clause_still_decomposed():
    """LLM results should still have when clauses decomposed."""
    host = FakeHost()
    llm_recs = [
        {
            "domain": "FA",
            "sdtm_variable": "FAORRES when FATESTCD=THDIAG",
            "sdtm_variable_type": "standard",
            "score": 0.8,
            "source": "LLM",
            "variable_name": "TESTVAR",
        }
    ]
    result = host._normalize_domain_recs("TEST", "TESTVAR", llm_recs, target_domain="FA", enforce_domain=True)
    assert len(result) == 1
    assert result[0]["sdtm_variable"] == "FAORRES"
    assert result[0]["testcd"] == "THDIAG"
    print(f"PASS: LLM when decomposed: sdtm_var={result[0]['sdtm_variable']}, testcd={result[0]['testcd']}")


if __name__ == "__main__":
    test_kb_pipe_pattern_preserved()
    test_kb_qval_when_qnam_decomposed()
    test_kb_ecoccur_pipe_pattern()
    test_llm_when_clause_still_decomposed()
    print("\nAll tests passed!")
