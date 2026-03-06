import sys
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "core").exists() and (parent / "scripts").exists():
            return parent
    return here.parents[2]


ROOT = _repo_root()
sys.path.insert(0, str(ROOT))
import core.scenario_runner as sr


class _DummyPage:
    def wait_for_timeout(self, ms: int):
        # Minimal time-sleep to simulate async wait
        import time

        time.sleep(min(0.01, ms / 1000.0))
    def query_selector(self, selector: str):
        # Return a single DummyElement if selector looks plausible
        sel = str(selector or "")
        if not sel:
            return None
        return DummyElement(sel)

    def query_selector_all(self, selector: str):
        # Return a short list of DummyElements for selector lists
        sel = str(selector or "")
        if not sel:
            return []
        # Heuristic: if selector contains role= or [aria-label], return 2 matches
        count = 2 if ("role=" in sel or "aria-label" in sel) else 1
        return [DummyElement(sel) for _ in range(count)]


class DummyBrowserSession:
    def __init__(self, *args, **kwargs):
        self._url = ""
        self.page = _DummyPage()
        self._fill_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def goto(self, url):
        self._url = str(url or "")

    def content(self):
        # Return a minimal but plausible HTML page for Google Flights flows
        return """
        <html><body>
        <input aria-label='From' role='combobox' />
        <input aria-label='To' role='combobox' />
        <button>Search</button>
        </body></html>
        """

    def screenshot(self, path: str, full_page: bool = True):
        # write a tiny PNG header so downstream code can open the file if needed
        try:
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
        except Exception:
            pass

    def fill_google_flights_combobox(self, *args, **kwargs):
        # Record attempt and simulate success using a selector string
        self._fill_calls.append((args, kwargs))
        return True, "[role='combobox'][aria-label*='From']"

    def __getattr__(self, name: str):
        # Provide permissive no-op implementations for other browser helpers used
        if name.startswith("fill_") or name.startswith("activate_"):
            def _noop(*a, **k):
                return False

            return _noop

        if name in {"click", "press", "close"}:
            def _ok(*a, **k):
                return True

            return _ok

        raise AttributeError(name)


class DummyElement:
    def __init__(self, selector: str):
        self.selector = selector

    def get_attribute(self, name: str, timeout: int = None):
        # Provide plausible attribute values for common attributes
        n = str(name or "").lower()
        if n in {"aria-label", "aria-valuetext", "value"}:
            return ""
        if n == "role":
            # extract role from selector if present
            if "role='combobox'" in self.selector or "role=combobox" in self.selector:
                return "combobox"
        return None

    def text_content(self, timeout: int = None):
        return ""

    def inner_text(self, timeout: int = None):
        return ""

    def click(self, *a, **k):
        return True

    def input_value(self, timeout: int = None):
        return ""


def run_smoke():
    # Monkeypatch the wrapper module so the implementation picks up this BrowserSession
    sr.BrowserSession = DummyBrowserSession
    # Monkeypatch LLM planner calls to avoid network calls during smoke
    try:
        import llm.code_model as code_model

        def _fake_generate_action_plan(*args, **kwargs):
            # return empty plan bundle
            return {"steps": [], "notes": []}

        def _fake_repair_action_plan(old_plan, html, *args, **kwargs):
            return {"steps": old_plan or [], "notes": []}

        code_model.generate_action_plan = _fake_generate_action_plan
        code_model.repair_action_plan = _fake_repair_action_plan
    except Exception:
        pass
    try:
        from datetime import datetime, timedelta, UTC
        depart_date = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
        html = sr.run_agentic_scenario(
            url="https://example.com",
            origin="AAA",
            dest="BBB",
            depart=depart_date,
        )
        print('SMOKE_OK', type(html), len(str(html or '')))
    except Exception as e:
        import traceback

        traceback.print_exc()
        print('SMOKE_FAILED', e)


if __name__ == '__main__':
    run_smoke()
