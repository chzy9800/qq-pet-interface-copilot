from __future__ import annotations

import json
import zipfile

from qqpet_app.diagnostics import create_diagnostic_bundle, sanitize_text


def test_sanitize_text_redacts_account_pet_email_path_and_secret():
    config = {
        "account": {"uin": "2360091679", "pet_id": "pet-secret-123"},
        "notifications": {"smtp_user": "owner@example.com", "token": "token-secret-123"},
    }
    raw = (
        "QQ 2360091679 pet_id=pet-secret-123 owner@example.com "
        "token-secret-123 C:\\Users\\RealName\\AppData"
    )
    cleaned = sanitize_text(raw, config)
    assert "2360091679" not in cleaned
    assert "pet-secret-123" not in cleaned
    assert "owner@example.com" not in cleaned
    assert "token-secret-123" not in cleaned
    assert "RealName" not in cleaned


def test_bundle_contains_only_sanitized_diagnostic_material(tmp_path):
    log_dir = tmp_path / "runs" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-08-14.log").write_text(
        "[12:00:00] 状态：金币 100，好友 小明，QQ 2360091679\n"
        "[12:00:01] 接口错误：QQ 2360091679 petId=pet-secret-123 返回空响应\n",
        encoding="utf-8",
    )
    config = {
        "account": {"uin": "2360091679", "pet_id": "pet-secret-123"},
        "mobile_protocol": {"enabled": True, "endpoint": "127.0.0.1:27042"},
        "notifications": {"enabled": True, "pushplus_token": "push-secret-123"},
    }
    output = create_diagnostic_bundle(
        tmp_path, config, tmp_path / "diagnostics.zip", log_dir=log_dir
    )
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "README.txt", "report.json", "settings-summary.json",
            "interface-catalog.json", "interface-errors.json", "diagnostic.log",
        }
        all_text = "\n".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
        )
        assert "2360091679" not in all_text
        assert "pet-secret-123" not in all_text
        assert "push-secret-123" not in all_text
        assert "好友 小明" not in all_text
        assert "返回空响应" in all_text
        report = json.loads(archive.read("report.json"))
        assert report["diagnostic_log_lines"] == 1
