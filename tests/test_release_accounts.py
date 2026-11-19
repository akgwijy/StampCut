import importlib
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from stampcut.core import settings as settings_mod
from stampcut.core.ffmpeg import Cancelled
from stampcut.core.models import Settings
from stampcut.core.publishing.credentials import CredentialStore
from stampcut.core.publishing.models import PublishError


def account_module():
    return importlib.import_module("stampcut.gui.release_accounts")


def make_dialog(qtbot, tmp_path):
    mod = account_module()
    store = CredentialStore(tmp_path / "account-vault.bin", protect=lambda data: data, unprotect=lambda data: data)
    dialog = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(dialog)
    return mod, store, dialog


def queued_jobs(dialog, monkeypatch):
    jobs = []
    monkeypatch.setattr(dialog.pool, "start", jobs.append)
    return jobs


def test_loads_encrypted_profiles_and_token_is_masked(qtbot, tmp_path):
    mod = account_module()
    store = CredentialStore(tmp_path / "account-vault.bin", protect=lambda data: data, unprotect=lambda data: data)
    store.put("youtube", {"client_file": "C:/oauth/client.json", "channel_id": "UCtest", "channel_title": "<b>Channel</b>", "refresh_token": "YT-REFRESH"})
    store.put("instagram", {"account_id": "123456", "username": "profile_name", "access_token": "IG-SECRET", "method": "url", "api_version": "v26.0"})
    dialog = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(dialog)
    assert dialog.yt_file_edit.text() == "C:/oauth/client.json"
    assert "<b>Channel</b>" in dialog.yt_account_label.text()
    assert dialog.yt_account_label.textFormat() == Qt.PlainText
    assert "profile_name" in dialog.ig_account_label.text()
    assert dialog.ig_account_label.textFormat() == Qt.PlainText
    assert dialog.ig_id_edit.text() == "123456"
    assert dialog.ig_token_edit.text() == "IG-SECRET"
    assert dialog.ig_token_edit.echoMode() == QLineEdit.Password
    assert dialog.ig_method_combo.currentData() == "url"
    assert dialog.message.textFormat() == Qt.PlainText
    assert "IG-SECRET" not in dialog.message.text()
    assert "YT-REFRESH" not in dialog.yt_account_label.text()
    assert not dialog.cancel_btn.isEnabled()


def test_method_selection_explains_local_file_and_url_requirements(qtbot, tmp_path):
    _, _, dialog = make_dialog(qtbot, tmp_path)
    assert dialog.ig_method_combo.currentData() == "unified"
    dialog.ig_method_combo.setCurrentIndex(dialog.ig_method_combo.findData("resumable"))
    assert "Facebook" in dialog.ig_method_help.text()
    assert "권한" in dialog.ig_method_help.text()
    dialog.ig_method_combo.setCurrentIndex(dialog.ig_method_combo.findData("url"))
    assert "HTTPS" in dialog.ig_method_help.text()
    assert "Instagram" in dialog.ig_method_help.text()
    assert "토큰" in dialog.ig_method_help.text()


def test_youtube_browse_only_selects_a_file_without_connecting(qtbot, tmp_path, monkeypatch):
    mod, _, dialog = make_dialog(qtbot, tmp_path)
    monkeypatch.setattr(mod.QFileDialog, "getOpenFileName", lambda *args: ("C:/oauth/client.json", "JSON"))
    monkeypatch.setattr(mod, "connect_youtube", lambda *args, **kwargs: pytest.fail("Browse must not authenticate"))
    dialog.yt_browse_btn.click()
    assert dialog.yt_file_edit.text() == "C:/oauth/client.json"
    assert not dialog.is_busy


def test_youtube_connect_persists_client_path_only_after_success_and_reopens(qtbot, tmp_path, monkeypatch):
    mod, store, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    path = tmp_path / "desktop.json"
    path.write_text("{}")
    dialog.yt_file_edit.setText(str(path))
    seen = []

    def connect(client_file, supplied_store, progress, cancel):
        assert supplied_store is store
        assert not cancel.is_set()
        seen.append(str(client_file))
        progress("auth", 100, 100, "연결 완료")
        credentials = {"channel_id": "UCnew", "channel_title": "New Channel", "refresh_token": "YT-SECRET", "client_id": "id"}
        supplied_store.put("youtube", credentials)
        return credentials

    monkeypatch.setattr(mod, "connect_youtube", connect)
    dialog.yt_connect_btn.click()
    assert dialog.is_busy and jobs
    assert store.get("youtube") == {}
    jobs[0].run()
    assert seen == [str(path)]
    assert not dialog.is_busy
    assert store.get("youtube")["client_file"] == str(path)
    assert store.get("youtube")["refresh_token"] == "YT-SECRET"
    assert "New Channel" in dialog.yt_account_label.text()
    reopened = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(reopened)
    assert reopened.yt_file_edit.text() == str(path)
    assert "New Channel" in reopened.yt_account_label.text()


@pytest.mark.parametrize("method", ["resumable", "url"])
def test_instagram_connect_uses_selected_method_and_verified_profile(qtbot, tmp_path, monkeypatch, method):
    mod, store, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.ig_id_edit.setText(" 123456 ")
    dialog.ig_token_edit.setText(" IG-SECRET ")
    dialog.ig_method_combo.setCurrentIndex(dialog.ig_method_combo.findData(method))
    seen = []

    def connect(account_id, access_token, selected_method, supplied_store, *, api_version="v26.0", progress, cancel):
        assert supplied_store is store
        seen.append((account_id, access_token, selected_method, api_version))
        credentials = {"account_id": account_id, "access_token": access_token, "method": selected_method, "api_version": api_version, "username": "verified_profile"}
        supplied_store.put("instagram", credentials)
        return credentials

    monkeypatch.setattr(mod, "connect_instagram", connect)
    dialog.ig_connect_btn.click()
    jobs[0].run()
    assert seen == [("123456", "IG-SECRET", method, "v26.0")]
    assert "verified_profile" in dialog.ig_account_label.text()
    assert dialog.ig_token_edit.echoMode() == QLineEdit.Password
    reopened = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(reopened)
    assert reopened.ig_method_combo.currentData() == method
    assert reopened.ig_id_edit.text() == "123456"
    assert reopened.ig_token_edit.text() == "IG-SECRET"


def test_failed_connection_keeps_existing_profile_credentials_and_client_path(qtbot, tmp_path, monkeypatch):
    mod, store, dialog = make_dialog(qtbot, tmp_path)
    previous = {"client_file": "C:/old.json", "channel_title": "Old Channel", "channel_id": "UCold", "refresh_token": "OLD-SECRET"}
    store.put("youtube", previous)
    dialog.close()
    dialog = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(dialog)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.yt_file_edit.setText("C:/new.json")

    def fail(*args, **kwargs):
        raise PublishError("Google 계정을 다시 연결하세요.")

    monkeypatch.setattr(mod, "connect_youtube", fail)
    dialog.yt_connect_btn.click()
    jobs[0].run()
    assert store.get("youtube") == previous
    assert "Old Channel" in dialog.yt_account_label.text()
    assert "다시 연결" in dialog.message.text()
    assert dialog.yt_connect_btn.isEnabled()
    assert not dialog.cancel_btn.isEnabled()


def test_busy_close_requests_cancel_and_keeps_widgets_until_worker_terminates(qtbot, tmp_path, monkeypatch):
    mod, _, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.ig_id_edit.setText("123456")
    dialog.ig_token_edit.setText("IG-SECRET")

    def connect(*args, progress, cancel, **kwargs):
        assert cancel.is_set()
        raise Cancelled()

    monkeypatch.setattr(mod, "connect_instagram", connect)
    dialog.show()
    dialog.ig_connect_btn.click()
    for control in (dialog.yt_file_edit, dialog.yt_browse_btn, dialog.yt_connect_btn, dialog.ig_id_edit, dialog.ig_token_edit, dialog.ig_method_combo, dialog.ig_connect_btn):
        assert not control.isEnabled()
    assert dialog.cancel_btn.isEnabled()
    dialog.close()
    assert jobs[0].cancel.is_set()
    assert dialog.isVisible() and dialog.is_busy
    jobs[0].run()
    assert not dialog.isVisible() and not dialog.is_busy


def test_cancel_button_cancels_without_closing_and_allows_retry(qtbot, tmp_path, monkeypatch):
    mod, _, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.ig_id_edit.setText("123456")
    dialog.ig_token_edit.setText("IG-SECRET")

    def connect(*args, progress, cancel, **kwargs):
        assert cancel.is_set()
        raise Cancelled()

    monkeypatch.setattr(mod, "connect_instagram", connect)
    dialog.show()
    dialog.ig_connect_btn.click()
    dialog.cancel_btn.click()
    assert jobs[0].cancel.is_set()
    jobs[0].run()
    assert dialog.isVisible() and not dialog.is_busy
    assert dialog.ig_connect_btn.isEnabled()


def test_empty_fields_never_queue_account_operations(qtbot, tmp_path, monkeypatch):
    _, _, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.yt_connect_btn.click()
    assert dialog.message.text()
    dialog.ig_connect_btn.click()
    assert dialog.message.text()
    assert jobs == []


def test_account_setup_never_creates_plain_settings_or_serializes_credentials(qtbot, tmp_path, monkeypatch):
    mod, _, dialog = make_dialog(qtbot, tmp_path)
    jobs = queued_jobs(dialog, monkeypatch)
    dialog.ig_id_edit.setText("123456")
    dialog.ig_token_edit.setText("IG-SECRET")

    def connect(account_id, access_token, method, store, *, api_version, progress, cancel):
        result = {"account_id": account_id, "access_token": access_token, "method": method, "username": "profile"}
        store.put("instagram", result)
        return result

    monkeypatch.setattr(mod, "connect_instagram", connect)
    dialog.ig_connect_btn.click()
    jobs[0].run()
    dialog.close()
    assert not settings_mod.settings_path().exists()
    plain = tmp_path / "plain-settings.json"
    settings_mod.save(Settings(), plain)
    assert "IG-SECRET" not in plain.read_text("utf-8")
    assert "access_token" not in plain.read_text("utf-8")



def test_disconnect_only_deletes_the_selected_platform_and_clears_masked_token(qtbot, tmp_path, monkeypatch):
    mod, store, initial = make_dialog(qtbot, tmp_path)
    store.put("youtube", {"channel_id": "UCtest", "channel_title": "Channel", "refresh_token": "YT-SECRET", "client_file": "C:/client.json"})
    store.put("instagram", {"account_id": "123", "username": "profile", "access_token": "IG-SECRET", "method": "url"})
    initial.close()
    dialog = mod.ReleaseAccountsDialog(store)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(mod, "connect_youtube", lambda *args, **kwargs: pytest.fail("Disconnect made a network request"))
    monkeypatch.setattr(mod, "connect_instagram", lambda *args, **kwargs: pytest.fail("Disconnect made a network request"))
    dialog.ig_disconnect_btn.click()
    assert store.get("instagram") == {}
    assert store.get("youtube")["refresh_token"] == "YT-SECRET"
    assert dialog.ig_token_edit.text() == ""
    assert dialog.ig_id_edit.text() == ""
    assert "profile" not in dialog.ig_account_label.text()
    assert not dialog.ig_disconnect_btn.isEnabled()
    dialog.yt_disconnect_btn.click()
    assert store.get("youtube") == {}
    assert dialog.yt_file_edit.text() == ""
    assert "Channel" not in dialog.yt_account_label.text()
    assert not dialog.yt_disconnect_btn.isEnabled()
