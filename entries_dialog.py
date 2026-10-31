"""Окно «Мои сайты и программы» (меню в трее): список, добавление и удаление без правки config.json руками."""
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget, QComboBox,
                               QTreeWidgetItem, QVBoxLayout)

STYLE = """
QDialog { background: #0a0612; color: #ffd2e1; }
QLabel { color: #ffd2e1; }
QTabWidget::pane { border: 1px solid #ff2260; }
QTabBar::tab { background: #1a0a1e; color: #ffd2e1; padding: 6px 14px; border: 1px solid #ff2260; }
QTabBar::tab:selected { background: #ff2260; color: #0a0612; }
QTreeWidget, QLineEdit { background: #120818; color: #ffd2e1; border: 1px solid #5a2a50;
                         selection-background-color: #ff2260; selection-color: #0a0612; }
QHeaderView::section { background: #1a0a1e; color: #28ff96; border: 0; padding: 4px; }
QPushButton { background: #1a0a1e; color: #ffd2e1; border: 1px solid #ff2260; padding: 5px 14px; }
QPushButton:hover { background: #ff2260; color: #0a0612; }
"""
URL_RE = re.compile(r"^(https?://)?[\w\-]+(\.[\w\-]+)*\.[a-zа-я]{2,}(:\d+)?(/\S*)?$", re.IGNORECASE)


def _clipboard_url():
    text = (QGuiApplication.clipboard().text() or "").strip()
    return text if URL_RE.match(text) else ""


class SiteForm(QDialog):
    """Название + адрес сайта. Адрес подставляется из буфера обмена, если там ссылка."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Новый сайт")
        self.setStyleSheet(STYLE)
        self.name = QLineEdit()
        self.name.setPlaceholderText("например: музыка")
        self.url = QLineEdit(_clipboard_url())
        self.url.setPlaceholderText("например: https://music.yandex.ru")
        form = QFormLayout(self)
        form.addRow(QLabel("Скопируй адрес сайта из браузера и придумай, как будешь его называть."))
        form.addRow("Название:", self.name)
        form.addRow("Адрес:", self.url)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(460, 0)

    def _check(self):
        url = self.url.text().strip()
        if not self.name.text().strip():
            QMessageBox.warning(self, "Капри", "Напиши название — то, что скажешь после «открой».")
        elif not URL_RE.match(url):
            QMessageBox.warning(self, "Капри", "Это не похоже на адрес сайта. Скопируй его из адресной строки браузера.")
        else:
            self.accept()

    def values(self):
        url = self.url.text().strip()
        return self.name.text().strip(), url if url.startswith("http") else "https://" + url


class ReplyForm(QDialog):
    """Своя фраза и ответ Капри на неё."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Новый ответ")
        self.setStyleSheet(STYLE)
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText("например: твой голос это проблема")
        self.answer = QLineEdit()
        self.answer.setPlaceholderText("например: Ну и иди ты.")
        form = QFormLayout(self)
        form.addRow(QLabel("Что ты говоришь Капри (после её имени) и что она ответит."))
        form.addRow("Фраза:", self.phrase)
        form.addRow("Ответ:", self.answer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(460, 0)

    def _check(self):
        if not self.phrase.text().strip() or not self.answer.text().strip():
            QMessageBox.warning(self, "Капри", "Заполни и фразу, и ответ.")
        else:
            self.accept()

    def values(self):
        return self.phrase.text().strip(), self.answer.text().strip()


class CommandForm(QDialog):
    """Пользовательская голосовая команда: фраза → безопасное действие выбранного типа."""

    ACTIONS = [
        ("app", "Запустить приложение / команду"),
        ("site", "Открыть сайт"),
        ("text", "Ввести текст в активное окно"),
        ("keys", "Нажать сочетание клавиш"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Новая команда Капри")
        self.setStyleSheet(STYLE)
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText("например: включи мой редактор")
        self.action = QComboBox()
        for key, label in self.ACTIONS:
            self.action.addItem(label, key)
        self.target = QLineEdit()
        self.target.setPlaceholderText("например: code или /usr/bin/spotify")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.action.currentIndexChanged.connect(self._update_hint)
        form = QFormLayout(self)
        form.addRow(QLabel("Выбери фразу, которую будешь говорить после имени Капри, и действие."))
        form.addRow("Голосовая фраза:", self.phrase)
        form.addRow("Что сделать:", self.action)
        form.addRow("Программа / данные:", self.target)
        form.addRow(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(500, 0)
        self._update_hint()

    def _update_hint(self, *_):
        kind = self.action.currentData()
        details = {
            "app": "Укажи имя запускаемой программы или команду с аргументами. Команда запускается без shell-подстановок.",
            "site": "Укажи полный адрес, например https://music.yandex.ru.",
            "text": "Этот текст будет введён в активное окно после произнесения фразы.",
            "keys": "Например: ctrl+shift+esc или alt+f4. Используй только знакомые сочетания.",
        }
        placeholders = {
            "app": "например: firefox или /usr/bin/spotify",
            "site": "https://music.yandex.ru",
            "text": "Текст, который нужно напечатать",
            "keys": "ctrl+shift+esc",
        }
        self.hint.setText(details[kind])
        self.target.setPlaceholderText(placeholders[kind])

    def _check(self):
        phrase, target = self.phrase.text().strip(), self.target.text().strip()
        if not phrase or not target:
            QMessageBox.warning(self, "Капри", "Заполни голосовую фразу и действие.")
            return
        kind = self.action.currentData()
        if kind == "site" and not re.match(r"^https?://\S+", target, re.I):
            QMessageBox.warning(self, "Капри", "Для сайта укажи адрес, начинающийся с http:// или https://.")
            return
        if kind == "keys" and not re.fullmatch(r"[\w +_-]+", target):
            QMessageBox.warning(self, "Капри", "Укажи сочетание клавиш, например ctrl+shift+esc.")
            return
        self.accept()

    def values(self):
        return self.phrase.text().strip(), {"action": self.action.currentData(), "target": self.target.text().strip()}


class EntriesDialog(QDialog):
    def __init__(self, actions):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.actions = actions
        self.setWindowTitle("Капри — мои сайты, программы и ответы")
        self.setStyleSheet(STYLE)
        self.tabs = QTabWidget()
        self.trees = {}
        for section, title, first, column in (("sites", "Сайты", "Название", "Адрес"),
                                              ("apps", "Программы", "Название", "Что открывает"),
                                              ("replies", "Ответы", "Фраза", "Ответ Капри"),
                                              ("commands", "Команды", "Голосовая фраза", "Действие")):
            tree = QTreeWidget()
            tree.setHeaderLabels([first, column])
            tree.setRootIsDecorated(False)
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.trees[section] = tree
            self.tabs.addTab(tree, title)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.tabs.currentChanged.connect(self._update_hint)
        self._update_hint()
        hint = self.hint
        add, remove, close = QPushButton("Добавить…"), QPushButton("Удалить"), QPushButton("Закрыть")
        add.clicked.connect(self.add)
        remove.clicked.connect(self.remove)
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self.tabs)
        lay.addLayout(row)
        self.resize(640, 520)
        self.refresh()

    def _book(self, section):
        return {"sites": self.actions.sites, "apps": self.actions.apps, "replies": self.actions.replies,
                "commands": self.actions.custom_commands}[section]

    def refresh(self):
        for section, tree in self.trees.items():
            book = self._book(section)
            tree.clear()
            for name in sorted(book):
                tree.addTopLevelItem(QTreeWidgetItem([name, str(book[name])]))

    def _section(self):
        return ("sites", "apps", "replies", "commands")[self.tabs.currentIndex()]

    def _update_hint(self, *_):
        section = self._section()
        if section == "replies":
            text = "Фраза — что ты говоришь Капри (можно чуть иначе), ответ — что она скажет в ответ. Работает сразу.\nГолосом: «Капри, когда я скажу „как дела“, отвечай „лучше всех“»."
        elif section == "commands":
            text = "Создай свою голосовую фразу и выбери действие. Например, «Капри, включи мой плеер». Команда сохраняется и работает сразу."
        else:
            text = "Название — то, что говоришь после «Капри, открой …». Изменения работают сразу."
        self.hint.setText(text)

    def _save(self, section, name, target):
        err = self.actions.remember(section, name, target)
        if err:
            QMessageBox.warning(self, "Капри", "Не получилось записать: " + err)
            return False
        self.refresh()
        return True

    def add(self):
        if self._section() == "commands":
            form = CommandForm(self)
            if form.exec():
                self._save("commands", *form.values())
            return
        if self._section() == "replies":
            form = ReplyForm(self)
            if form.exec():
                self._save("replies", *form.values())
            return
        if self._section() == "sites":
            form = SiteForm(self)
            if form.exec():
                self._save("sites", *form.values())
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбери программу или её ярлык", os.path.join(os.path.expanduser("~"), "Desktop"),
            "Программы и ярлыки (*.exe *.lnk *.url *.bat *.cmd);;Все файлы (*)")
        if not path:
            return
        guess = os.path.splitext(os.path.basename(path))[0].lower()
        name, ok = _ask_name(self, guess)
        if ok and name:
            self._save("apps", name, os.path.normpath(path))

    def remove(self):
        tree = self.trees[self._section()]
        item = tree.currentItem()
        if not item:
            QMessageBox.information(self, "Капри", "Сначала выбери строку в списке.")
            return
        name = item.text(0)
        box = QMessageBox(QMessageBox.Icon.Question, "Капри", f"Забыть «{name}»?", parent=self)
        yes = box.addButton("Забыть", QMessageBox.ButtonRole.YesRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.NoRole)
        box.exec()
        if box.clickedButton() is yes:
            self._save(self._section(), name, None)


def _ask_name(parent, guess):
    from PySide6.QtWidgets import QInputDialog
    name, ok = QInputDialog.getText(parent, "Название",
                                    "Как ты будешь её называть? (то, что скажешь после «открой»)", text=guess)
    return name.strip(), ok
