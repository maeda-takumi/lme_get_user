# ui_main.py
import sys
import sqlite3
import threading
import csv, os
import json
from datetime import datetime, timedelta
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QPlainTextEdit, QMessageBox, QDialog, QDialogButtonBox, QTextEdit,
    
    QDateEdit, QTimeEdit, QGroupBox, QGridLayout, QLineEdit, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QDate, QTime
from PySide6.QtWidgets import QSizePolicy

import time
# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# 既存ロジック
from main import initialize_db, scrape_user_list
from message import initialize_message_table, scrape_messages
from tags import scrape_tags
# from tags import initialize_tag_table, scrape_tags

# スタイル
from style import app_stylesheet, apply_card_shadow
import threading
from uploader import upload_db_ftps               # ← 既存のFTPSアップローダ
import pprint
from update_support_from_sheet import main as update_support_sync_main

SERVER_UPLOAD_CONFIG = {
    "user": "ss911157",
    "password": "fmmrsumv",
    "hosts": ["totalappworks.com"],
    "remote_dir": "/totalappworks.com/public_html/support_aori/data",
    "remote_name": "lstep_users.db",
    "local_file": "lstep_users.db",
}


def upload_server_db(logger=None) -> dict:
    """ローカルDBを設定済みのFTPSサーバーへアップロードする。"""
    if logger:
        logger.message.emit("🟡 サーバーへアップロードを開始します…")

    debug = upload_db_ftps(**SERVER_UPLOAD_CONFIG)

    if logger:
        if debug.get("success"):
            logger.message.emit("✅ アップロード完了（安全な置換方式）")
            logger.message.emit(pprint.pformat(debug, width=100))
        else:
            logger.message.emit("❌ アップロード失敗（詳細は下記）")
            logger.message.emit(pprint.pformat(debug, width=100))

    return debug

LOGIN_PROFILE_DIR = os.path.join(os.getcwd(), ".chrome_profile", "lstep_login")


def create_chrome_options(detach: bool = False) -> Options:
    """ログインセッションを永続化する Chrome オプションを作成する。"""
    os.makedirs(LOGIN_PROFILE_DIR, exist_ok=True)
    options = Options()
    options.add_argument(f"--user-data-dir={LOGIN_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    if detach:
        options.add_experimental_option("detach", True)
    return options

def export_tables_to_csv(db_path: str = "lstep_users.db", out_dir: str = "exports") -> dict:
    """
    users と messages を CSV 出力（UTF-8 with BOM）する。
    戻り値: {"users": <path>, "messages": <path>}
    """
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_users = os.path.join(out_dir, f"users_{ts}.csv")
    out_messages = os.path.join(out_dir, f"messages_{ts}.csv")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # users
        cur.execute("SELECT * FROM users")
        cols_u = [d[0] for d in cur.description]
        rows_u = cur.fetchall()
        friend_value_idx = cols_u.index("friend_value") if "friend_value" in cols_u else None
        friend_value_labels = []
        friend_value_label_set = set()
        parsed_friend_values = []

        if friend_value_idx is not None:
            for row in rows_u:
                raw = row[friend_value_idx]
                parsed = {}
                if raw:
                    try:
                        json_obj = json.loads(raw)
                        if isinstance(json_obj, dict):
                            parsed = {str(k): v for k, v in json_obj.items()}
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}

                parsed_friend_values.append(parsed)
                for label in parsed.keys():
                    if label not in friend_value_label_set:
                        friend_value_label_set.add(label)
                        friend_value_labels.append(label)

            cols_u_export = [c for c in cols_u if c != "friend_value"] + friend_value_labels
            rows_u_export = []
            for row, parsed in zip(rows_u, parsed_friend_values):
                base = [v for i, v in enumerate(row) if i != friend_value_idx]
                extra = [parsed.get(label, "") for label in friend_value_labels]
                rows_u_export.append(base + extra)
        else:
            cols_u_export = cols_u
            rows_u_export = rows_u

        with open(out_users, "w", encoding="utf-8-sig", newline="") as fw:
            w = csv.writer(fw)
            w.writerow(cols_u_export)
            w.writerows(rows_u_export)

        # messages
        cur.execute("SELECT * FROM messages")
        cols_m = [d[0] for d in cur.description]
        rows_m = cur.fetchall()
        with open(out_messages, "w", encoding="utf-8-sig", newline="") as fw:
            w = csv.writer(fw)
            w.writerow(cols_m)
            w.writerows(rows_m)

        return {"users": out_users, "messages": out_messages, "users_count": len(rows_u_export), "messages_count": len(rows_m)}
    finally:
        conn.close()

# ===================== モーダル：続行ゲート =====================
class ContinueDialog(QDialog):
    def __init__(self, title: str, instructions: str, proceed_text: str = "続行", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 360)

        lay = QVBoxLayout(self)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("TitleLabel")
        lay.addWidget(title_lbl)

        card = QFrame(); card.setObjectName("Card")
        v = QVBoxLayout(card)
        tip = QLabel("以下の手順を完了したら［続行］を押してください。")
        v.addWidget(tip)

        inst = QTextEdit()
        inst.setReadOnly(True)
        inst.setPlainText(instructions)
        # inst.setMinimumHeight(180)
        v.addWidget(inst)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText(proceed_text)
        btns.button(QDialogButtonBox.Cancel).setText("キャンセル")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        lay.addWidget(card)

# ===================== ロガー/シグナル =====================
class UILogger(QObject):
    message = Signal(str)
    enable_ui = Signal(bool)
    show_info = Signal(str, str)
    show_error = Signal(str, str)
    friends_loaded = Signal()
    # (title, instructions, proceed_event, cancel_event, proceed_text)
    open_gate = Signal(str, str, object, object, str)

# ===================== ユーティリティ =====================
def clear_tables(include_messages: bool = True):
    """users / messages テーブルの中身をクリア"""
    conn = sqlite3.connect("lstep_users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM users")
    if include_messages:
        cur.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

# ===================== スクレイピング処理（別スレッド） =====================
def load_users_for_selection(db_path: str = "lstep_users.db") -> list[dict]:
    """UIの友だち選択リストに表示する users データを読み込む。"""
    try:
        initialize_db()
    except sqlite3.Error:
        return []

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, line_name, display_name, new_message_date, support, tags
            FROM users
            ORDER BY id ASC
            """
        )
        return [
            {
                "id": row[0],
                "line_name": row[1] or "",
                "display_name": row[2] or "",
                "new_message_date": row[3] or "",
                "support": row[4] or "",
                "tags": row[5] or "",
            }
            for row in cur.fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()

def run_scraping(logger: UILogger, target_date: str | None = None):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 初期化中…")
        initialize_db()
        initialize_message_table()

        logger.message.emit("🟡 既存データをクリアします（users / messages）")
        # clear_tables()

        logger.message.emit("🟡 ブラウザを起動します…")
        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        logger.message.emit("🟡 自動ログインセッションで友だちリストへ移動します…")
        driver.get("https://step.lme.jp/basic/friendlist")

        logger.message.emit("🟡 一覧を取得中…")
        time.sleep(8)
        scrape_user_list(driver)

        if target_date:
            logger.message.emit(f"🟡 メッセージ取得を開始します（対象日: {target_date}）…")
        else:
            logger.message.emit("🟡 メッセージ取得を開始します（全期間）…")
        scrape_messages(driver, logger, target_date=target_date)

        logger.message.emit("🟢 スクレイピング完了。サポート担当の同期を開始します…")
        try:
            # スプレッドシート → users.support を更新（B列=LINE名、F列=担当者）
            update_support_sync_main()   # ← 添付の main() をそのまま実行
            logger.message.emit("✅ サポート担当の同期が完了しました。")
        except Exception as e:
            logger.message.emit(f"❌ サポート担当の同期に失敗: {e}")
            # 続行は可能なので、アプリは止めずにログだけ出す
            
        logger.message.emit("🎉 全処理が完了しました！")
        return True
    except Exception as e:
        logger.message.emit(f"❌ エラー: {e}")
        logger.show_error.emit("エラー", f"{e}")
        return False
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)

def run_friend_list_scraping(logger: UILogger):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 友だちリスト取得の初期化中…")
        initialize_db()

        logger.message.emit("🟡 ブラウザを起動します…")
        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        logger.message.emit("🟡 自動ログインセッションで友だちリストへ移動します…")
        driver.get("https://step.lme.jp/basic/friendlist")

        logger.message.emit("🟡 友だち一覧を取得中…")
        time.sleep(8)
        scrape_user_list(driver)
        logger.message.emit("✅ 友だちリスト取得が完了しました。")
        logger.friends_loaded.emit()
    except Exception as e:
        logger.message.emit(f"❌ 友だちリスト取得エラー: {e}")
        logger.show_error.emit("友だちリスト取得エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)


def run_selected_friend_message_scraping(
    logger: UILogger,
    selected_user_ids: list[int],
    target_date: str | None = None,
):
    driver = None
    try:
        logger.enable_ui.emit(False)
        initialize_db()
        initialize_message_table()

        logger.message.emit(f"🟡 選択友だち {len(selected_user_ids)}件のメッセージ取得を開始します…")
        if target_date:
            logger.message.emit(f"📅 対象日: {target_date}")
        else:
            logger.message.emit("📅 対象日指定なし: 全期間を取得します")

        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        scrape_messages(
            driver,
            logger,
            target_date=target_date,
            user_ids=selected_user_ids,
            use_resume=False,
        )
        logger.message.emit("🎉 選択友だちのメッセージ取得が完了しました！")
    except Exception as e:
        logger.message.emit(f"❌ 選択友だちメッセージ取得エラー: {e}")
        logger.show_error.emit("選択友だちメッセージ取得エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)

def run_polling(logger: UILogger, execute_time: QTime, stop_event: threading.Event):
    """指定時刻になったら当日分スクレイピングを毎日実行する。"""
    execute_hour = execute_time.hour()
    execute_minute = execute_time.minute()
    execute_time_text = f"{execute_hour:02d}:{execute_minute:02d}"
    logger.message.emit(f"🟢 ポーリング開始: 毎日 {execute_time_text} に当日分を取得します。")

    now = datetime.now()
    next_run_at = now.replace(hour=execute_hour, minute=execute_minute, second=0, microsecond=0)
    if next_run_at <= now:
        next_run_at += timedelta(days=1)
    logger.message.emit(f"🕒 次回実行予定: {next_run_at.strftime('%Y-%m-%d %H:%M:%S')}")

    while not stop_event.is_set():
        now = datetime.now()
        if now >= next_run_at:
            target_date = now.date().strftime("%Y-%m-%d")

            logger.message.emit(
                f"🟡 ポーリング実行時刻に到達: 当日({target_date})のデータ取得を開始します。"
            )
            if run_scraping(logger, target_date=target_date):
                logger.message.emit("🟡 ポーリング取得完了: 自動アップロードを開始します。")
                upload_server_db(logger)
            else:
                logger.message.emit("⚠️ ポーリング取得に失敗したため、自動アップロードをスキップします。")

            next_run_at += timedelta(days=1)
            logger.message.emit(f"🕒 次回実行予定: {next_run_at.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.message.emit("🟢 ポーリング待機に戻ります。")

        stop_event.wait(timeout=1)

    logger.message.emit("🛑 ポーリングを停止しました。")

def run_tag_scraping(logger: UILogger):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 初期化中…")
        initialize_db()
        logger.message.emit("🟡 既存データをクリアします（users）")
        # clear_tables(include_messages=False)

        logger.message.emit("🟡 ブラウザを起動します…")
        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        driver.get("https://step.lme.jp/")

        # ---- UIゲート（OKで続行 / キャンセルで中断）----
        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) ブラウザでLステップにログインしてください。\n"
            "2) 対象の『友達リスト』まで手動で移動してください。\n"
            "3) 画面が開けたら、このポップアップの［続行］を押してください。\n\n"
            "※［キャンセル］を押すと処理を中断します。"
        )
        logger.open_gate.emit("ログイン＆移動のお願い", instructions, proceed_event, cancel_event, "続行")
        # どちらかが押されるまで待つ（ポーリングで両方監視）
        while True:
            if proceed_event.wait(timeout=0.1):
                break
            if cancel_event.is_set():
                logger.message.emit("🛑 ユーザー操作によりキャンセルされました。")
                return  # finally へ

        logger.message.emit("🟡 一覧を取得中…")
        time.sleep(8)
        scrape_user_list(driver)

        logger.message.emit("🟡 タグ取得を開始します…")
        scrape_tags(driver, logger)

        logger.message.emit("🎉 タグ取得の処理が完了しました！")
    except Exception as e:
        logger.message.emit(f"❌ エラー: {e}")
        logger.show_error.emit("エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)

def run_login_session_save(logger: UILogger):
    """手動ログイン後にセッションを保存する専用フロー。"""
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 ログイン保存モードを開始します…")
        logger.message.emit(f"🟡 保存先プロファイル: {LOGIN_PROFILE_DIR}")

        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")

        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) 開いたブラウザで手動ログインしてください。\n"
            "2) ログイン完了を確認してください。\n"
            "3) このポップアップで［ログイン情報を保存して終了］を押してください。\n\n"
            "※［キャンセル］を押すと保存せず終了します。"
        )
        logger.open_gate.emit(
            "ログイン情報保存",
            instructions,
            proceed_event,
            cancel_event,
            "ログイン情報を保存して終了",
        )

        while True:
            if proceed_event.wait(timeout=0.1):
                break
            if cancel_event.is_set():
                logger.message.emit("🛑 ログイン保存をキャンセルしました。")
                return

        logger.message.emit("✅ ログイン情報を保存しました。")
    except Exception as e:
        logger.message.emit(f"❌ ログイン保存中にエラー: {e}")
        logger.show_error.emit("ログイン保存エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)
# ===================== メインウィンドウ =====================
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LMessage ユーティリティ")
        self.setMinimumSize(1200, 760)
        self.setStyleSheet(app_stylesheet())
        self.logger = UILogger()
        self.logger.message.connect(self.append_log)
        self.logger.enable_ui.connect(self.set_controls_enabled)
        
        self.analysis_window = None   # ← GC対策で保持
        self.logger.show_info.connect(self.on_show_info)
        self.logger.show_error.connect(self.on_show_error)
        self.logger.friends_loaded.connect(self.load_friends_from_db)
        self.logger.open_gate.connect(self.on_open_gate)
        self.polling_stop_event = None
        self.polling_thread = None
        self.polling_active = False
        self._build()
        self.load_friends_from_db()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(14)

        # タイトル
        title = QLabel("LMessage ユーティリティ")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        sub_title = QLabel("スクレイピング・アップロード・エクスポートを用途別にまとめました。")
        sub_title.setObjectName("SubTitleLabel")
        root.addWidget(sub_title)

        # 本体エリア（4フレーム構成）
        # 縦に積み過ぎると低い画面で要素が重なりやすいため、用途別に横方向へ分割する。
        body = QHBoxLayout()
        body.setSpacing(14)

        first_frame = QFrame()
        first_frame.setObjectName("Card")
        first_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        first_layout = QVBoxLayout(first_frame)
        first_layout.setSpacing(14)

        second_frame = QFrame()
        second_frame.setObjectName("Card")
        second_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        second_layout = QVBoxLayout(second_frame)
        second_layout.setSpacing(14)

        third_frame = QFrame()
        third_frame.setObjectName("Card")
        third_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        third_layout = QVBoxLayout(third_frame)
        third_layout.setSpacing(14)

        fourth_frame = QFrame()
        fourth_frame.setObjectName("Card")
        fourth_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fourth_layout = QVBoxLayout(fourth_frame)
        fourth_layout.setSpacing(10)

        run_group = QGroupBox("データ取得")
        run_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        run_grid = QGridLayout(run_group)

        self.btn_friend_list_scrape = QPushButton("友だちリスト取得")
        self.btn_friend_list_scrape.clicked.connect(self.on_click_friend_list_scrape)
        run_grid.addWidget(self.btn_friend_list_scrape, 0, 0, 1, 2)

        self.btn_scrape = QPushButton("スクレイピング実行（全友だち）")
        self.btn_scrape.clicked.connect(self.on_click_scrape)
        run_grid.addWidget(self.btn_scrape, 1, 0, 1, 2)

        self.date_input = QDateEdit()
        self.date_input.setDisplayFormat("yyyy-MM-dd")
        self.date_input.setCalendarPopup(True)
        self.date_input.setSpecialValueText("未指定（全期間）")
        self.date_input.setMinimumDate(QDate(2000, 1, 1))
        self.date_input.setDate(self.date_input.minimumDate())
        self.date_input.setToolTip("対象日を指定すると、その日のメッセージのみ取得します。未指定なら全期間を取得します。")
        run_grid.addWidget(QLabel("対象日"), 2, 0)
        run_grid.addWidget(self.date_input, 2, 1)

        self.btn_tag_scrape = QPushButton("タグ取得実行")
        self.btn_tag_scrape.clicked.connect(self.on_click_tag_scrape)
        run_grid.addWidget(self.btn_tag_scrape, 3, 0, 1, 2)

        self.btn_login_save = QPushButton("ログイン保存実行")
        self.btn_login_save.clicked.connect(self.on_click_login_save)
        run_grid.addWidget(self.btn_login_save, 4, 0, 1, 2)

        polling_group = QGroupBox("ポーリング")
        polling_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        polling_grid = QGridLayout(polling_group)
        polling_grid.addWidget(QLabel("実行時刻"), 0, 0)
        self.polling_time_input = QTimeEdit()
        self.polling_time_input.setDisplayFormat("HH:mm")
        now_time = QTime.currentTime()
        self.polling_time_input.setTime(now_time.addSecs(-now_time.second()))
        self.polling_time_input.setToolTip("毎日この時刻に当日分のスクレイピングを実行します。")
        polling_grid.addWidget(self.polling_time_input, 0, 1)

        self.btn_polling_start = QPushButton("ポーリング開始")
        self.btn_polling_start.clicked.connect(self.on_click_polling_start)
        polling_grid.addWidget(self.btn_polling_start, 1, 0)
        self.btn_polling_stop = QPushButton("ポーリング停止")
        self.btn_polling_stop.setObjectName("SecondaryButton")
        self.btn_polling_stop.clicked.connect(self.on_click_polling_stop)
        self.btn_polling_stop.setEnabled(False)
        polling_grid.addWidget(self.btn_polling_stop, 1, 1)

        self.polling_status_label = QLabel("停止中")
        self.polling_status_label.setObjectName("StatusIdle")
        polling_grid.addWidget(self.polling_status_label, 2, 0, 1, 2)

        maintenance_group = QGroupBox("メンテナンス")
        maintenance_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        maintenance_layout = QVBoxLayout(maintenance_group)
        self.btn_upload = QPushButton("サーバーアップロード実行")
        self.btn_upload.clicked.connect(self.on_click_upload)
        maintenance_layout.addWidget(self.btn_upload)
        self.btn_force_unlock = QPushButton("UIロック解除")
        self.btn_force_unlock.setObjectName("SecondaryButton")
        self.btn_force_unlock.setToolTip("処理が固まってUIが無効化された場合に、入力可能な状態へ戻します。")
        self.btn_force_unlock.clicked.connect(self.on_click_force_unlock)
        maintenance_layout.addWidget(self.btn_force_unlock)

        export_group = QGroupBox("出力")
        export_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        export_row = QHBoxLayout(export_group)
        self.btn_analysis = QPushButton("分析（別UI起動）")
        # self.btn_analysis.clicked.connect(self.on_click_analysis)
        # row3.addWidget(self.btn_analysis)

        # ▼ 追加：CSVエクスポートボタン
        self.btn_export = QPushButton("CSVエクスポート（users / messages）")
        self.btn_export.clicked.connect(self.on_click_export)
        export_row.addWidget(self.btn_export)

        friend_group = QGroupBox("友だち選択")
        friend_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        friend_layout = QVBoxLayout(friend_group)

        self.friend_search_input = QLineEdit()
        self.friend_search_input.setPlaceholderText("LINE名・表示名・担当者・タグで検索")
        self.friend_search_input.textChanged.connect(self.filter_friend_table)
        friend_layout.addWidget(self.friend_search_input)

        self.friend_table = QTableWidget(0, 6)
        self.friend_table.setHorizontalHeaderLabels(["ID", "LINE名", "表示名", "新規メッセージ日", "担当者", "タグ"])
        self.friend_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.friend_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.friend_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.friend_table.verticalHeader().setVisible(False)
        self.friend_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.friend_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.friend_table.setMinimumHeight(260)
        friend_layout.addWidget(self.friend_table)

        friend_buttons = QHBoxLayout()
        self.btn_reload_friends = QPushButton("一覧再読み込み")
        self.btn_reload_friends.setObjectName("SecondaryButton")
        self.btn_reload_friends.clicked.connect(self.load_friends_from_db)
        friend_buttons.addWidget(self.btn_reload_friends)
        self.btn_select_all_friends = QPushButton("表示中を全選択")
        self.btn_select_all_friends.setObjectName("SecondaryButton")
        self.btn_select_all_friends.clicked.connect(self.select_visible_friends)
        friend_buttons.addWidget(self.btn_select_all_friends)
        self.btn_clear_friend_selection = QPushButton("選択解除")
        self.btn_clear_friend_selection.setObjectName("SecondaryButton")
        self.btn_clear_friend_selection.clicked.connect(self.friend_table.clearSelection)
        friend_buttons.addWidget(self.btn_clear_friend_selection)
        friend_layout.addLayout(friend_buttons)

        self.btn_selected_friend_messages = QPushButton("選択した友だちのやり取りを取得")
        self.btn_selected_friend_messages.clicked.connect(self.on_click_selected_friend_messages)
        friend_layout.addWidget(self.btn_selected_friend_messages)

        first_layout.addWidget(run_group)
        first_layout.addWidget(polling_group)
        first_layout.addStretch(1)

        second_layout.addWidget(friend_group)

        third_layout.addWidget(maintenance_group)
        third_layout.addWidget(export_group)
        third_layout.addStretch(1)

        # ログビュー（白背景＋濃い文字）

        log_label = QLabel("ログ")
        fourth_layout.addWidget(log_label)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        fourth_layout.addWidget(self.log)

        body.addWidget(first_frame, 1)
        body.addWidget(second_frame, 2)
        body.addWidget(third_frame, 1)
        body.addWidget(fourth_frame, 2)
        for frame in (first_frame, second_frame, third_frame, fourth_frame):
            apply_card_shadow(frame)

        root.addLayout(body, 1)
    def _selected_date_text(self):
        if self.date_input.date() != self.date_input.minimumDate():
            return self.date_input.date().toString("yyyy-MM-dd")
        return None

    @Slot()
    def load_friends_from_db(self):
        users = load_users_for_selection()
        self.friend_table.setRowCount(0)
        for user in users:
            row = self.friend_table.rowCount()
            self.friend_table.insertRow(row)
            values = [
                user["id"],
                user["line_name"],
                user["display_name"],
                user["new_message_date"],
                user["support"],
                user["tags"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.UserRole, int(user["id"]))
                self.friend_table.setItem(row, col, item)
        self.filter_friend_table(self.friend_search_input.text())
        self.logger.message.emit(f"👥 友だち一覧を読み込みました: {len(users)}件")

    @Slot(str)
    def filter_friend_table(self, text: str):
        keyword = (text or "").strip().lower()
        for row in range(self.friend_table.rowCount()):
            searchable = []
            for col in range(1, self.friend_table.columnCount()):
                item = self.friend_table.item(row, col)
                searchable.append(item.text().lower() if item else "")
            self.friend_table.setRowHidden(row, bool(keyword) and keyword not in " ".join(searchable))

    def select_visible_friends(self):
        self.friend_table.clearSelection()
        for row in range(self.friend_table.rowCount()):
            if not self.friend_table.isRowHidden(row):
                self.friend_table.selectRow(row)

    def get_selected_friend_ids(self) -> list[int]:
        selected_ids = set()
        for index in self.friend_table.selectionModel().selectedRows():
            if self.friend_table.isRowHidden(index.row()):
                continue
            item = self.friend_table.item(index.row(), 0)
            if item:
                selected_ids.add(int(item.data(Qt.UserRole) or item.text()))
        return sorted(selected_ids)
    
    def run_upload(self):
        try:
            self.logger.enable_ui.emit(False)
            debug = upload_server_db(self.logger)

            # 成否で分岐表示
            if debug.get("success"):
                self.logger.show_info.emit("完了", "アップロードが完了しました。")
            else:
                self.logger.show_error.emit("アップロード失敗", debug.get("error", "原因不明"))
        except Exception as e:
            self.logger.message.emit(f"❌ 例外: {e}")
            self.logger.show_error.emit("アップロード失敗", f"{e}")
        finally:
            self.logger.enable_ui.emit(True)
    # ---------- UI slots ----------
    def set_controls_enabled(self, enabled: bool):
        self.btn_friend_list_scrape.setEnabled(enabled)
        self.btn_scrape.setEnabled(enabled)
        self.btn_tag_scrape.setEnabled(enabled)
        self.btn_upload.setEnabled(enabled)
        self.btn_login_save.setEnabled(enabled)
        # self.btn_analysis.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)   # ← 追加
        self.btn_reload_friends.setEnabled(enabled)
        self.btn_select_all_friends.setEnabled(enabled)
        self.btn_clear_friend_selection.setEnabled(enabled)
        self.btn_selected_friend_messages.setEnabled(enabled)
        self.friend_search_input.setEnabled(enabled)
        self.friend_table.setEnabled(enabled)
        self.date_input.setEnabled(enabled)
        self.polling_time_input.setEnabled(enabled and not self.polling_active)
        self.btn_polling_start.setEnabled(enabled and not self.polling_active)
        self.btn_polling_stop.setEnabled(self.polling_active)
        # 緊急解除ボタンは常時有効（ロック復旧用）
        self.btn_force_unlock.setEnabled(True)

    def append_log(self, text: str):
        self.log.appendPlainText(text)

    def run_export(self):
        try:
            self.logger.enable_ui.emit(False)
            self.logger.message.emit("🟡 CSVエクスポートを開始します…")
            result = export_tables_to_csv(db_path="lstep_users.db", out_dir="exports")
            self.logger.message.emit(f"✅ エクスポート完了: users={result['users_count']}件, messages={result['messages_count']}件")
            self.logger.message.emit(f"📄 保存先: {result['users']}\n📄 保存先: {result['messages']}")
            self.logger.show_info.emit("完了", f"CSVを出力しました。\n{result['users']}\n{result['messages']}")
        except Exception as e:
            self.logger.message.emit(f"❌ エクスポート失敗: {e}")
            self.logger.show_error.emit("エクスポート失敗", f"{e}")
        finally:
            self.logger.enable_ui.emit(True)

    def on_click_export(self):
        t = threading.Thread(target=self.run_export, daemon=True)
        t.start()

    @Slot(str, str)
    def on_show_info(self, title, text):
        QMessageBox.information(self, title, text)

    @Slot(str, str)
    def on_show_error(self, title, text):
        QMessageBox.critical(self, title, text)

    @Slot(str, str, object, object)
    @Slot(str, str, object, object, str)
    def on_open_gate(
        self,
        title: str,
        instructions: str,
        proceed_event: object,
        cancel_event: object,
        proceed_text: str = "続行",
    ):
        dlg = ContinueDialog(title, instructions, proceed_text, self)

        dlg.setStyleSheet(app_stylesheet())
        res = dlg.exec()
        if res == QDialog.Accepted:
            proceed_event.set()
        else:
            cancel_event.set()             # ← キャンセルを明示
            self.set_controls_enabled(True)  # 念のため即座にUIを戻す

    # ---------- Actions ----------
    def on_click_friend_list_scrape(self):
        t = threading.Thread(target=run_friend_list_scraping, args=(self.logger,), daemon=True)
        t.start()
    def on_click_scrape(self):
        selected_date = self._selected_date_text()
        t = threading.Thread(target=run_scraping, args=(self.logger, selected_date), daemon=True)
        t.start()

    def on_click_selected_friend_messages(self):
        selected_user_ids = self.get_selected_friend_ids()
        if not selected_user_ids:
            self.logger.message.emit("⚠️ メッセージ取得対象の友だちを選択してください。")
            self.logger.show_error.emit("友だち未選択", "メッセージ取得対象の友だちを1人以上選択してください。")
            return
        selected_date = self._selected_date_text()
        t = threading.Thread(
            target=run_selected_friend_message_scraping,
            args=(self.logger, selected_user_ids, selected_date),
            daemon=True,
        )
        t.start()

    def on_click_polling_start(self):
        if self.polling_active:
            self.logger.message.emit("ℹ️ すでにポーリング実行中です。")
            return

        self.polling_stop_event = threading.Event()
        execute_time = self.polling_time_input.time()
        self.polling_thread = threading.Thread(
            target=run_polling,
            args=(self.logger, execute_time, self.polling_stop_event),
            daemon=True,
        )
        self.polling_active = True
        self.polling_status_label.setText(f"稼働中（毎日 {execute_time.toString('HH:mm')} 実行）")
        self.polling_status_label.setObjectName("StatusRunning")
        self.polling_status_label.style().unpolish(self.polling_status_label)
        self.polling_status_label.style().polish(self.polling_status_label)
        self.set_controls_enabled(True)
        self.polling_thread.start()

    def on_click_polling_stop(self):
        if not self.polling_active:
            return
        self.polling_stop_event.set()
        self.polling_active = False
        self.polling_status_label.setText("停止中")
        self.polling_status_label.setObjectName("StatusIdle")
        self.polling_status_label.style().unpolish(self.polling_status_label)
        self.polling_status_label.style().polish(self.polling_status_label)
        self.set_controls_enabled(True)

    def on_click_tag_scrape(self):
        t = threading.Thread(target=run_tag_scraping, args=(self.logger,), daemon=True)
        t.start()
        
    def on_click_upload(self):
        t = threading.Thread(target=self.run_upload, daemon=True)
        t.start()

    def on_click_force_unlock(self):
        self.logger.message.emit("⚠️ 手動でUIロックを解除しました。")
        self.set_controls_enabled(True)

    def on_click_login_save(self):
        t = threading.Thread(target=run_login_session_save, args=(self.logger,), daemon=True)
        t.start()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SUP-ADMIN")
    app.setWindowIcon(QIcon("icons/icon.png"))  # exe化時は相対/同梱パスに合わせる
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
