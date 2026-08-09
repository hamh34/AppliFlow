import json

from appliflow.config import AlertConfig
from appliflow.diagnose import (
    FAIL,
    OK,
    SKIP,
    Check,
    check_alert_config,
    check_config,
    check_credentials,
    check_token,
    render,
)

VALID_CONFIG = 'spreadsheet_id = "1TJfh4Sn"\nworksheet = "Applications"\n'


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestConfigCheck:
    def test_passes_on_a_valid_file(self, tmp_path):
        check = check_config(write(tmp_path, "config.toml", VALID_CONFIG))
        assert check.state == OK

    def test_missing_file_names_the_fix(self, tmp_path):
        check = check_config(tmp_path / "config.toml")
        assert check.state == FAIL
        assert "config.example.toml" in check.fix

    def test_empty_spreadsheet_id_is_caught(self, tmp_path):
        check = check_config(write(tmp_path, "config.toml", 'spreadsheet_id = ""'))
        assert check.state == FAIL
        assert "spreadsheet_id" in check.detail

    def test_malformed_toml_is_reported_not_raised(self, tmp_path):
        check = check_config(write(tmp_path, "config.toml", 'spreadsheet_id = "unclosed'))
        assert check.state == FAIL
        assert "parse" in check.detail

    def test_does_not_leak_the_whole_sheet_id(self, tmp_path):
        long_id = "A" * 44
        check = check_config(
            write(tmp_path, "config.toml", f'spreadsheet_id = "{long_id}"')
        )
        assert long_id not in check.detail


class TestCredentialsCheck:
    def test_desktop_client_passes(self, tmp_path):
        path = write(tmp_path, "credentials.json", json.dumps({"installed": {"client_id": "x"}}))
        assert check_credentials(path).state == OK

    def test_web_client_is_rejected_with_a_specific_fix(self, tmp_path):
        """The most common setup mistake; it must not be reported vaguely."""
        path = write(tmp_path, "credentials.json", json.dumps({"web": {"client_id": "x"}}))
        check = check_credentials(path)
        assert check.state == FAIL
        assert "Web application" in check.detail
        assert "Desktop app" in check.fix

    def test_missing_file(self, tmp_path):
        check = check_credentials(tmp_path / "credentials.json")
        assert check.state == FAIL
        assert "Desktop app" in check.fix

    def test_corrupt_json(self, tmp_path):
        path = write(tmp_path, "credentials.json", "{not json")
        assert check_credentials(path).state == FAIL

    def test_unrecognized_shape(self, tmp_path):
        path = write(tmp_path, "credentials.json", json.dumps({"something": 1}))
        assert check_credentials(path).state == FAIL


class TestTokenCheck:
    def test_valid_token_passes(self, tmp_path):
        path = write(tmp_path, "token.json", json.dumps({"refresh_token": "r"}))
        assert check_token(path).state == OK

    def test_missing_token_points_at_init(self, tmp_path):
        check = check_token(tmp_path / "token.json")
        assert check.state == FAIL
        assert "init" in check.fix

    def test_token_without_refresh_token_is_caught(self, tmp_path):
        path = write(tmp_path, "token.json", json.dumps({"token": "abc"}))
        check = check_token(path)
        assert check.state == FAIL
        assert "refresh" in check.detail

    def test_corrupt_token(self, tmp_path):
        path = write(tmp_path, "token.json", "}{")
        assert check_token(path).state == FAIL


class TestAlertConfigCheck:
    def test_enabled_reports_the_window(self):
        check = check_alert_config(AlertConfig(enabled=True, lookback_days=14))
        assert check.state == OK
        assert "14d" in check.detail

    def test_disabled_is_a_skip_not_a_failure(self):
        assert check_alert_config(AlertConfig(enabled=False)).state == SKIP


class TestRender:
    def test_shows_fixes_only_for_failures(self):
        out = render([
            Check("good", OK, "fine", fix="should not appear"),
            Check("bad", FAIL, "broken", fix="do this"),
        ])
        assert "should not appear" not in out
        assert "fix: do this" in out

    def test_marks_each_state_distinctly(self):
        out = render([Check("a", OK), Check("b", FAIL), Check("c", SKIP)])
        assert "[ok]" in out and "[--]" in out and "[..]" in out

    def test_empty_input(self):
        assert render([]) == ""
