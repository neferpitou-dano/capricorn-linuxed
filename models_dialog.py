import json
import logging
import urllib.request

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QProgressBar, QMessageBox
)

URL = "http://127.0.0.1:11434"
log = logging.getLogger("capri")


def installed_models():
    req = urllib.request.Request(URL + "/api/tags")
    with urllib.request.urlopen(req, timeout=3) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [m["name"] for m in data.get("models", [])]


class PullWorker(QThread):
    progress = Signal(str, int)
    failed = Signal(str)
    finished_model = Signal(str)

    def __init__(self, name):
        super().__init__()
        self.name = name

    def run(self):
        try:
            payload = json.dumps({
                "name": self.name,
                "stream": True
            }).encode("utf-8")
            request = urllib.request.Request(
                URL + "/api/pull",
                data=payload,
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(request, timeout=None) as response:
                for raw in response:
                    if not raw.strip():
                        continue
                    item = json.loads(raw.decode("utf-8"))
                    if item.get("error"):
                        raise RuntimeError(item["error"])

                    status = item.get("status", "Загрузка")
                    total = item.get("total", 0)
                    done = item.get("completed", 0)
                    percent = int(done * 100 / total) if total else -1
                    self.progress.emit(status, percent)

            self.finished_model.emit(self.name)
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelsDialog(QDialog):
    def __init__(self, local, remember_model, parent=None):
        super().__init__(parent)
        self.local = local
        self.remember_model = remember_model
        self.worker = None

        self.setWindowTitle("Локальный ИИ — модели")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Установленные модели Ollama:"))

        row = QHBoxLayout()
        self.models = QComboBox()
        self.refresh_button = QPushButton("Обновить")
        row.addWidget(self.models, 1)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)

        layout.addWidget(QLabel("Скачать модель по имени:"))
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("Например, qwen3:4b")
        layout.addWidget(self.model_name)

        self.download_button = QPushButton("Скачать модель")
        layout.addWidget(self.download_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status = QLabel("Готово")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.activate_button = QPushButton("Использовать выбранную")
        self.close_button = QPushButton("Закрыть")
        buttons.addWidget(self.activate_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.refresh_button.clicked.connect(self.refresh)
        self.activate_button.clicked.connect(self.activate)
        self.download_button.clicked.connect(self.download)
        self.close_button.clicked.connect(self.close)

        self.refresh()

    def refresh(self):
        current = self.local.model
        try:
            names = installed_models()
        except Exception as exc:
            self.status.setText("Ollama недоступна")
            QMessageBox.warning(
                self, "Ollama",
                f"Не удалось получить список моделей:\n{exc}"
            )
            return

        self.models.clear()
        self.models.addItems(names)
        index = self.models.findText(current)
        if index >= 0:
            self.models.setCurrentIndex(index)

        if names:
            self.status.setText(f"Установлено моделей: {len(names)}")
        else:
            self.status.setText("Моделей пока нет")

    def activate(self):
        name = self.models.currentText()
        if not name:
            QMessageBox.information(
                self, "Локальный ИИ", "Сначала скачай модель."
            )
            return

        self.local.model = name
        self.local.history.clear()
        self.remember_model(name)
        self.status.setText(f"Выбрана модель: {name}")
        self.accept()

    def download(self):
        if self.worker and self.worker.isRunning():
            return

        name = self.model_name.text().strip()
        if not name:
            QMessageBox.information(
                self, "Скачать модель",
                "Введи имя модели, например qwen3:4b."
            )
            return

        self.download_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.activate_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText("Подготовка загрузки…")

        self.worker = PullWorker(name)
        self.worker.progress.connect(self.on_progress)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished_model.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, status, percent):
        self.status.setText(status)
        if percent >= 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        else:
            self.progress.setRange(0, 0)

    def on_failed(self, error):
        self.progress.setRange(0, 100)
        self.download_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.activate_button.setEnabled(True)
        self.status.setText("Ошибка загрузки")
        QMessageBox.warning(
            self, "Не удалось скачать модель", error
        )

    def on_finished(self, name):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.download_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.activate_button.setEnabled(True)
        self.model_name.clear()
        self.status.setText(f"Модель {name} скачана")
        self.refresh()
        index = self.models.findText(name)
        if index >= 0:
            self.models.setCurrentIndex(index)
