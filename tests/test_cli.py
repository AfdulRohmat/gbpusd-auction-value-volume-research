from gbpusd_research.cli import main


def test_config_check(capsys) -> None:
    assert main(["config-check"]) == 0
    assert capsys.readouterr().out.strip() == "Configuration valid"


def test_show_config(capsys) -> None:
    assert main(["show-config"]) == 0
    output = capsys.readouterr().out
    assert '"symbol": "GBPUSD"' in output
    assert '"timezone": "Europe/London"' in output


def test_download_rejects_date_outside_configured_range(capsys) -> None:
    assert main(["download", "--date", "2023-01-01"]) == 2
    assert capsys.readouterr().out == ""


def test_tag_sessions_reports_missing_m5(capsys) -> None:
    assert main(["tag-sessions", "--date", "2024-01-03"]) == 1
    assert capsys.readouterr().out == ""
