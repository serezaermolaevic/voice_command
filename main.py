import tkinter as tk
from tkinter import ttk,messagebox, filedialog
import json
import os
import sys
import ctypes
import threading
import subprocess
import webbrowser
import re
import time
from urllib.parse import quote

try:
    import speech_recognition as sr
    SPEECH_AVALIBALE = True
    SPEECH_IMPORT_ERROR = None
except ImportError as e:
    SPEECH_AVALIBALE = False
    SPEECH_IMPORT_ERROR = str(e)

try:
    import pyttsx3
    TTS_AVALIBALE = True
except ImportError:
    TTS_AVALIBALE = False

try:
    import pyperclip
    CLIPBOARD_AVALIBALE = True
except ImportError:
    CLIPBOARD_AVALIBALE = False

try:
    import pyautogui
    AUTOSEND_AVALIBALE = True
except ImportError:
    AUTOSEND_AVALIBALE = False

# Сколько секунд ждать после открытия ссылки чата, прежде чем нажать
# Enter (даём время Telegram открыться и получить фокус окна).
# Если за это время пользователь тронет мышь/клавиатуру или переключит
# окно - Enter может уйти не туда, поэтому задержка намеренно нескромная.
AUTOSEND_DELAY_SECONDS = 3.0
    
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

# Google Speech иногда распознаёт заимствованные слова/бренды латиницей,
# даже когда язык распознавания — русский. Словарь приводит такие слова
# к единому (кириллическому) виду перед сравнением с командами.
# При необходимости просто дополните этот словарь своими словами.
WORD_NORMALIZATION = {
    'telegram': 'телеграм',
    'google': 'гугл',
    'youtube': 'ютуб',
    'whatsapp': 'вотсап',
    'chrome': 'хром',
    'yandex': 'яндекс',
    'browser': 'браузер',
    'spotify': 'спотифай',
    'discord': 'дискорд',
    'instagram': 'инстаграм',
}

def normalize_text(text):
    for latin, cyrillic in WORD_NORMALIZATION.items():
        text = re.sub(rf'\b{latin}\b', cyrillic, text, flags=re.IGNORECASE)
    return text

def get_resource_path(filename):
    return os.path.join(get_base_path(), filename)

class CommandManager():
    def __init__(self, filename = 'commands.json'):
        self.filename = get_resource_path(filename)
        self.commands = self.load()
    
    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.commands, f , ensure_ascii=False, indent=4)
            return True
        except:
            return False
    def add(self, command, action):
        self.commands[command] = action
        return self.save()
    
    def delete(self,command):
        if command in self.commands:
            del self.commands[command]
            return self.save()
        return False
    def edit(self, old_command, new_command, new_action):
        if old_command in self.commands:
            del self.commands[old_command]
            self.commands[new_command] = new_action
            return self.save()
        return False
    
    def get_all(self):
        return self.commands

class ContactManager():
    """
    Хранит словарь контактов для команд "напиши ..." / "позвони ...".

    Формат contacts.json:
    {
        "мама": {"platform": "telegram", "value": "durov", "aliases": ["маме", "маму"]},
        "паша": {"platform": "discord", "value": "123456789012345678", "aliases": ["паше", "пашу"]}
    }

    platform: "telegram" - value это username (без @)
              "discord"  - value это числовой ID пользователя
    aliases: другие словоформы имени, которые тоже нужно распознавать
             (голосовой ввод не умеет сам склонять слова обратно в именительный падеж)
    """
    def __init__(self, filename='contacts.json'):
        self.filename = get_resource_path(filename)
        self.contacts = self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.contacts, f, ensure_ascii=False, indent=4)
            return True
        except:
            return False

    def add(self, name, platform, value, aliases=None):
        self.contacts[name] = {
            "platform": platform,
            "value": value,
            "aliases": aliases or []
        }
        return self.save()

    def delete(self, name):
        if name in self.contacts:
            del self.contacts[name]
            return self.save()
        return False

    def edit(self, old_name, new_name, platform, value, aliases=None):
        if old_name in self.contacts:
            del self.contacts[old_name]
            self.contacts[new_name] = {
                "platform": platform,
                "value": value,
                "aliases": aliases or []
            }
            return self.save()
        return False

    def get_all(self):
        return self.contacts

    def find_in_text(self, text):
        """
        Ищет упоминание контакта в тексте (по имени или его словоформам).
        Возвращает (имя_контакта, остаток_текста_после_имени) или (None, text).
        Сначала пробует более длинные совпадения, чтобы "димы петровой"
        не срезалось до "димы", если такой отдельный контакт тоже есть.
        """
        text = text.strip()
        candidates = []
        for name, info in self.contacts.items():
            forms = [name] + info.get("aliases", [])
            for form in forms:
                form = form.strip().lower()
                if not form:
                    continue
                pattern = r'^' + re.escape(form) + r'\b'
                m = re.match(pattern, text, flags=re.IGNORECASE)
                if m:
                    candidates.append((len(form), name, text[m.end():].strip()))
        if not candidates:
            return None, text
        # Берём самое длинное совпадение словоформы
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, name, remainder = candidates[0]
        return name, remainder

class CallButtonConfig:
    """
    Хранит откалиброванные координаты кнопок звонка на экране отдельно
    для обычного (voice) и видео (video) звонка, чтобы команды "позвони"
    и "видеозвони" могли попробовать кликнуть по нужной кнопке.

    ВАЖНО: это не официальный способ дозвона, а грубая симуляция клика
    по фиксированным координатам экрана. Работает только если каждый
    раз, когда открывается чат, нужная кнопка оказывается ровно в той
    же точке экрана, что и во время калибровки - то есть окно Telegram
    должно открываться в том же месте и с тем же размером
    (максимизированным на весь экран - самый надёжный вариант).
    Если изменить размер/положение окна, разрешение экрана или обновить
    Telegram (может сдвинуть интерфейс) - придётся откалибровать заново.
    """
    def __init__(self, filename='call_button.json'):
        self.filename = get_resource_path(filename)
        self.data = self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
            return True
        except:
            return False

    def set_position(self, x, y, call_type="voice"):
        self.data[call_type] = {"x": x, "y": y}
        return self.save()

    def get_position(self, call_type="voice"):
        entry = self.data.get(call_type)
        if entry and "x" in entry and "y" in entry:
            return entry["x"], entry["y"]
        return None

    def is_calibrated(self, call_type="voice"):
        return self.get_position(call_type) is not None

class VoiceEngine:
    # Формы глагола "написать" и "позвонить", которые нужно распознавать
    # в начале команды, чтобы включить "умную" обработку контакта.
    WRITE_VERBS = ["напиши", "напишите", "написать", "отправь", "отправить"]
    CALL_VERBS = ["позвони", "позвоните", "позвонить", "набери"]
    VIDEO_CALL_VERBS = ["видеозвони", "видеопозвони", "видеозвоните"]
    # Слово "видео" перед обычным глаголом звонка тоже означает видеозвонок,
    # например: "видео позвони маме"
    VIDEO_PREFIX = "видео"
    HANGUP_VERBS = ["сбрось", "сброс", "положи", "заверши", "останови", "отбой", "завершить"]
    # Команды закрытия ВСЕГО приложения Джарвис (не путать с HANGUP_VERBS,
    # которые сбрасывают только текущий звонок). Используем только
    # однозначные возвратные формы, чтобы не пересекаться с другими
    # командами ("заверши звонок" и т.п.)
    EXIT_VERBS = ["закройся", "выключись", "отключись", "завершись"]
    # Закрытие ДРУГОГО, стороннего приложения - того, что сейчас
    # активно/открыто на экране (не самого Джарвиса).
    CLOSE_WINDOW_VERBS = ["закрой", "закройте"]
    # Если после "закрой" явно упоминается сам Джарвис - это на самом
    # деле команда закрытия приложения, а не активного окна.
    SELF_TARGETS = ["джарвис", "джарвиса", "себя"]

    # Поиск в Google по голосу: "загугли <запрос>", "найди в гугле <запрос>"
    SEARCH_VERBS = ["загугли", "погугли", "нагугли", "гугли"]

    # Словесные названия знаков препинания -> сами символы. Используется
    # в сообщениях для команды "напиши", чтобы можно было продиктовать
    # знак голосом (например "знак вопроса") вместо того, чтобы он
    # попадал в текст как слова. Ключи проверяются от более длинных
    # словосочетаний к более коротким, чтобы "восклицательный знак" не
    # обрывался на "знак".
    PUNCTUATION_WORDS = {
        "знак вопроса": "?",
        "вопросительный знак": "?",
        "восклицательный знак": "!",
        "знак восклицания": "!",
        "троеточие": "...",
        "многоточие": "...",
        "открывающая скобка": "(",
        "закрывающая скобка": ")",
        "открыть скобку": "(",
        "закрыть скобку": ")",
        "новая строка": "\n",
        "с новой строки": "\n",
        "точка с запятой": ";",
        "точка": ".",
        "запятая": ",",
        "запятую": ",",
        "двоеточие": ":",
        "тире": "-",
        "дефис": "-",
        "кавычки": '"',
        "кавычка": '"',
        "процент": "%",
        "решётка": "#",
        "решетка": "#",
        "звёздочка": "*",
        "звездочка": "*",
        "нижнее подчёркивание": "_",
        "нижнее подчеркивание": "_",
        "амперсанд": "&",
        "собака": "@",
        "слэш": "/",
        "обратный слэш": "\\",
    }

    def __init__(self, manager, contacts=None, call_button=None):
        self.manager = manager
        self.contacts = contacts  # ContactManager или None
        self.call_button = call_button  # CallButtonConfig или None
        self.is_listening = False
        self.is_active = False
        
        self.settings_file = get_resource_path('settings.json')
        self.keyword = self.load_keyword()
        
        self.callback = None
        self.exit_callback = None  # Функция для закрытия ВСЕГО приложения (устанавливается извне)
        
        if SPEECH_AVALIBALE:
            self.recognizer = sr.Recognizer()
        else:
            self.recognizer = None
        if TTS_AVALIBALE:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty('rate', 180)
                self.engine.setProperty('volume', 0.9)
            except:
                self.engine = False
        else:
            self.engine = None

    def load_keyword(self):
        """Загружает ключевое слово из settings.json. По умолчанию - 'джарвис'."""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('keyword', 'джарвис')
            except:
                return 'джарвис'
        return 'джарвис'

    def save_keyword(self, new_keyword):
        """Сохраняет новое ключевое слово в settings.json."""
        new_keyword = new_keyword.strip().lower()
        if not new_keyword:
            return False
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump({'keyword': new_keyword}, f, ensure_ascii=False, indent=4)
            self.keyword = new_keyword
            return True
        except:
            return False

    def _auto_send_enter(self, delay=AUTOSEND_DELAY_SECONDS):
        """
        В фоновом потоке ждёт немного, пока откроется/получит фокус
        приложение (Telegram Desktop или веб-версия в браузере), затем
        программно нажимает Enter, чтобы отправить уже подставленное
        в поле ввода сообщение.

        ВАЖНО: это не официальный API, а симуляция нажатия клавиши на
        уровне всей системы. Работает надёжно только если в момент
        нажатия активным (в фокусе) окном действительно является чат
        с уже готовым текстом - то есть пока идёт задержка, нельзя
        трогать мышь/клавиатуру и переключать окна, иначе Enter уйдёт
        в другое приложение.
        """
        if not AUTOSEND_AVALIBALE:
            self.update_log("⚠️ Авто-отправка недоступна: не установлен модуль pyautogui")
            return

        def _worker():
            time.sleep(delay)
            try:
                pyautogui.press('enter')
                self.update_log("📤 Сообщение отправлено автоматически")
            except Exception as e:
                self.update_log(f"⚠️ Не удалось авто-отправить сообщение: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def speak(self, text):
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except:
                pass
    def listen_loop(self):
        if not self.recognizer:
            return
        while self.is_listening:
            try:
                with sr.Microphone() as source:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.3)
                    audio = self.recognizer.listen(source)
                    try:
                        text = self.recognizer.recognize_google(audio, language='ru-RU').lower() 
                        text = normalize_text(text)
                        print(f"Распознано: {text}")
                        if self.keyword in text:
                            self.update_log("Активирован")
                            self.speak("Слушаю")
                            
                            command_text = text.replace(self.keyword, "").strip()
                            command_text = re.sub(r'[^\w\s]', '', command_text).strip()
                            if command_text:
                                # Сначала проверяем "умные" команды с контактом
                                # (напиши/позвони кому-то), и только если они
                                # не подошли - ищем обычную команду из списка.
                                if self.try_smart_contact_command(command_text):
                                    pass
                                else:
                                    # Ищем команду в списке
                                    for command, action in self.manager.get_all().items():
                                        if command.lower() in command_text:
                                            # Нашли команду - выполняем
                                            self.execute_action(action)
                                            self.update_log(f"✅ Выполнено: {command}")
                                            self.speak(f"Выполняю {command}")
                                            break
                                    else:
                                        # Команда не найдена
                                        self.update_log(f"❌ Команда не найдена: {command_text}")
                                        self.speak("Команда не найдена")
                            else:
                                self.update_log("ℹ️ Скажите команду после 'Джарвис'")
                                self.speak("Скажите команду")
                        
                    except sr.UnknownValueError:
                        # Речь не распознана - игнорируем
                        pass
                    except sr.RequestError:
                        # Ошибка подключения к Google
                        self.update_log("⚠️ Ошибка интернета")
                        
            except Exception as e:
                # Другие ошибки
                self.update_log(f"⚠️ Ошибка: {str(e)[:30]}")

    def execute_action(self, action):
        """
        Выполняет действие, связанное с командой.
        
        Поддерживаемые типы действий (для каждого шага):
        1. Веб-ссылка (http/https) - открывается в браузере
        2. Путь к файлу - открывается через проводник
        3. Системная команда - выполняется через консоль
        
        Одна команда может запускать НЕСКОЛЬКО действий сразу -
        для этого в поле "Действие" они перечисляются через " | ",
        например: notepad.exe | https://youtube.com | C:\\Games\\game.exe
        """
        steps = [step.strip() for step in action.split("|") if step.strip()]
        self.update_log(f"🔧 Найдено действий в команде: {len(steps)} → {steps}")
        for i, step in enumerate(steps):
            try:
                if step.startswith(("http://", "https://")):
                    # Веб-ссылка
                    webbrowser.open(step)
                    self.update_log(f"✅ Открыта ссылка: {step}")
                elif os.path.exists(step):
                    # Файл или папка
                    os.startfile(step)
                    self.update_log(f"✅ Запущено: {step}")
                else:
                    # Системная команда
                    subprocess.Popen(step, shell=True)
                    self.update_log(f"✅ Выполнена команда: {step}")
            except Exception as e:
                self.update_log(f"⚠️ Ошибка выполнения '{step}': {e}")

            # Небольшая пауза между запусками, чтобы Windows/приложения
            # не "терялись" при одновременном старте нескольких программ
            if i < len(steps) - 1:
                time.sleep(0.5)

    def try_smart_contact_command(self, command_text):
        """
        Проверяет, не является ли command_text командой вида
        "напиши <контакт> <сообщение>", "позвони <контакт>" или
        "сбрось звонок".

        Возвращает True, если команда была распознана и обработана
        (даже если обработка завершилась ошибкой - например, контакт
        не найден), False - если это вообще не команда с контактом,
        и тогда listen_loop должен продолжить обычный поиск по списку
        команд.
        """
        words = command_text.split()
        if not words:
            return False

        first_word = words[0].lower()

        # Закрытие всего приложения - проверяем раньше остальных команд,
        # не требует ни контактов, ни активного звонка.
        if first_word in self.EXIT_VERBS:
            return self._handle_exit_command()

        # "Закрой ..." - если явно про Джарвиса, это тоже выход из
        # приложения; иначе - закрытие активного стороннего окна.
        if first_word in self.CLOSE_WINDOW_VERBS:
            rest = " ".join(words[1:]).strip().lower()
            if any(target in rest for target in self.SELF_TARGETS):
                return self._handle_exit_command()
            return self._handle_close_window_command()

        # Поиск в Google - не требует контактов, проверяем раньше них.
        if first_word in self.SEARCH_VERBS:
            query = " ".join(words[1:]).strip()
            return self._handle_search_command(query)

        if first_word == "найди":
            query = " ".join(words[1:]).strip()
            # Убираем необязательный префикс вроде "в гугле"/"в интернете"
            query = re.sub(r'^(в\s+гугле|в\s+google|в\s+интернете)\s+', '', query, flags=re.IGNORECASE)
            return self._handle_search_command(query)

        # Сброс звонка не требует контакта - кнопка сброса уже видна
        # на экране во время активного звонка, поэтому эту команду
        # проверяем раньше остальных и не требуем self.contacts.
        if first_word in self.HANGUP_VERBS:
            return self._handle_hangup_command()

        if not self.contacts:
            return False

        second_word = words[1].lower() if len(words) > 1 else ""

        if first_word in self.WRITE_VERBS:
            rest = " ".join(words[1:]).strip()
            return self._handle_write_command(rest)

        # Видеозвонок: либо отдельное слово ("видеозвони"), либо
        # "видео" + обычный глагол звонка ("видео позвони маме")
        if first_word in self.VIDEO_CALL_VERBS:
            rest = " ".join(words[1:]).strip()
            return self._handle_call_command(rest, call_type="video")

        if first_word == self.VIDEO_PREFIX and second_word in self.CALL_VERBS:
            rest = " ".join(words[2:]).strip()
            return self._handle_call_command(rest, call_type="video")

        if first_word in self.CALL_VERBS:
            rest = " ".join(words[1:]).strip()
            return self._handle_call_command(rest, call_type="voice")

        return False

    def _handle_write_command(self, rest):
        if not rest:
            self.update_log("ℹ️ Скажите: 'напиши <контакт> <сообщение>'")
            self.speak("Кому и что написать?")
            return True

        contact_name, message = self.contacts.find_in_text(rest)
        if not contact_name:
            self.update_log(f"❌ Контакт не найден в: {rest}")
            self.speak("Не нашёл такой контакт")
            return True

        if not message:
            self.update_log(f"ℹ️ Что написать контакту '{contact_name}'?")
            self.speak(f"Что написать {contact_name}?")
            return True

        info = self.contacts.get_all()[contact_name]
        message = self._apply_punctuation(message)
        self.execute_write(contact_name, info, message)
        return True

    def _apply_punctuation(self, text):
        """
        Заменяет продиктованные голосом названия знаков препинания на
        сами символы (например "запятая" -> ",", "знак вопроса" -> "?"),
        чтобы можно было надиктовать полноценно оформленное сообщение.

        Сначала заменяются более длинные словосочетания ("знак вопроса"),
        затем более короткие ("точка"), чтобы длинные фразы не обрывались
        раньше времени. После замены убираются лишние пробелы вокруг
        знаков препинания.
        """
        if not text:
            return text

        result = text
        # Сначала более длинные словосочетания (несколько слов), потом
        # однословные - иначе "знак вопроса" может превратиться в
        # "? вопроса", если сначала заменить более короткое "вопроса".
        sorted_phrases = sorted(self.PUNCTUATION_WORDS.keys(),
                               key=lambda p: len(p.split()), reverse=True)
        for phrase in sorted_phrases:
            symbol = self.PUNCTUATION_WORDS[phrase]
            pattern = r'\b' + re.escape(phrase) + r'\b'
            # Замену делаем через функцию (lambda), а не через строку:
            # если строку заменыпередать напрямую, re.sub трактует в ней
            # обратный слэш и группы вида \1 как спецсимволы регулярки,
            # из-за чего символ "\" (обратный слэш) в PUNCTUATION_WORDS
            # ломал замену ошибкой "bad escape (end of pattern)".
            result = re.sub(pattern, lambda m, s=symbol: s, result, flags=re.IGNORECASE)

        # Убираем пробел перед знаками препинания: "привет , как дела ?"
        # -> "привет, как дела?"
        result = re.sub(r'\s+([.,!?;:%\)])', r'\1', result)
        # Убираем пробел сразу после открывающей скобки: "( текст" -> "(текст"
        result = re.sub(r'\(\s+', '(', result)
        # Схлопываем повторные пробелы, оставшиеся после замен
        result = re.sub(r'[ \t]+', ' ', result)

        return result.strip()

    def _handle_call_command(self, rest, call_type="voice"):
        verb_hint = "Кому позвонить?" if call_type == "voice" else "Кому видеозвонить?"
        if not rest:
            self.update_log(f"ℹ️ Скажите: '{'позвони' if call_type=='voice' else 'видеозвони'} <контакт>'")
            self.speak(verb_hint)
            return True

        contact_name, _ = self.contacts.find_in_text(rest)
        if not contact_name:
            self.update_log(f"❌ Контакт не найден в: {rest}")
            self.speak("Не нашёл такой контакт")
            return True

        info = self.contacts.get_all()[contact_name]
        self.execute_call(contact_name, info, call_type=call_type)
        return True

    def _handle_exit_command(self):
        """
        Обрабатывает голосовую команду закрытия ВСЕГО приложения
        Джарвис (например "Джарвис, закройся" или "Джарвис, выключись").

        В отличие от команды выключения микрофона (кнопка на экране),
        эта команда завершает работу программы целиком - через
        exit_callback, который устанавливает VoiceAssistantApp.
        """
        self.update_log("👋 Закрываю приложение по голосовой команде...")
        self.speak("Выключаюсь. До встречи!")
        if self.exit_callback:
            # Небольшая пауза, чтобы фраза озвучилась до закрытия окна
            def _worker():
                time.sleep(1.0)
                self.exit_callback()
            threading.Thread(target=_worker, daemon=True).start()
        else:
            self.update_log("⚠️ Не удалось закрыть приложение: exit_callback не установлен")
        return True

    def _handle_close_window_command(self):
        """
        Закрывает активное (то есть находящееся в фокусе) окно другого
        приложения - отправляет ему системное сообщение WM_CLOSE, как
        будто пользователь сам нажал на крестик. Это НЕ принудительное
        завершение процесса ("Снять задачу") - если в приложении есть
        несохранённые данные, оно может показать свой обычный диалог
        "Сохранить изменения?".

        ВАЖНО: "активное окно" - это то, что в данный момент реально
        имеет фокус в Windows. Если в момент голосовой команды в фокусе
        случайно оказалось само окно Джарвиса (например, вы только что
        кликнули по нему) - закрыт будет именно Джарвис. Чтобы закрыть
        именно Джарвиса, используйте отдельную команду "закройся" /
        "выключись", либо явно скажите "закрой Джарвиса".
        """
        if not sys.platform.startswith("win"):
            self.update_log("⚠️ Закрытие активного окна поддерживается только в Windows")
            self.speak("Эта команда работает только в Windows")
            return True
        try:
            WM_CLOSE = 0x0010
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                self.update_log("⚠️ Не удалось определить активное окно")
                self.speak("Не вижу активное окно")
                return True
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            self.update_log("🗙 Отправлена команда закрытия активному окну")
            self.speak("Закрываю окно")
        except Exception as e:
            self.update_log(f"⚠️ Не удалось закрыть окно: {e}")
        return True

    def _handle_search_command(self, query):
        """
        Открывает Google с уже готовым поисковым запросом и сразу
        показывает результаты - без набора текста и без нажатия Enter,
        так как ссылка вида google.com/search?q=... сама выполняет
        поиск при открытии.
        """
        if not query:
            self.update_log("ℹ️ Скажите: 'загугли <запрос>' или 'найди в гугле <запрос>'")
            self.speak("Что найти?")
            return True

        query = self._apply_punctuation(query)
        url = f"https://www.google.com/search?q={quote(query)}"
        webbrowser.open(url)
        self.update_log(f"🔍 Ищу в Google: {query}")
        self.speak(f"Ищу: {query}")
        return True

    def _handle_hangup_command(self):
        """
        Обрабатывает голосовую команду сброса/завершения звонка -
        кликает по откалиброванной точке "hangup", если она задана.
        Не требует контакта: подразумевается, что звонок уже идёт
        и кнопка сброса видна на экране.
        """
        if not AUTOSEND_AVALIBALE:
            self.update_log("📵 Сброс звонка недоступен: модуль pyautogui не установлен")
            self.speak("Сброс звонка недоступен, нажмите сами")
            return True

        position = self.call_button.get_position("hangup") if self.call_button else None
        if not position:
            self.update_log("📵 Кнопка сброса звонка не откалибрована - "
                             "см. '📍 Откалибровать кнопки звонка'")
            self.speak("Кнопка сброса не откалибрована, нажмите сами")
            return True

        self.update_log(f"📵 Сбрасываю звонок через {AUTOSEND_DELAY_SECONDS:.0f} сек")
        self.speak("Сбрасываю звонок")

        def _worker():
            time.sleep(AUTOSEND_DELAY_SECONDS)
            try:
                pyautogui.click(*position)
                self.update_log("📵 Клик по кнопке сброса выполнен")
            except Exception as e:
                self.update_log(f"⚠️ Не удалось сбросить звонок: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def execute_call(self, contact_name, info, call_type="voice"):
        """
        Открывает чат/профиль контакта и, если соответствующая кнопка
        звонка (обычного или видео) была откалибрована, см.
        CallButtonConfig, пытается кликнуть по ней автоматически.

        ВАЖНО: ни Telegram, ни Discord не дают запустить настоящий
        VoIP-звонок конкретному человеку одной лишь ссылкой - это
        не поддерживается ни одним мессенджером через URL. Клик по
        фиксированным координатам - это ненадёжный обходной приём:
        если окно Telegram открывается не в том же месте/размере, что
        во время калибровки, клик попадёт мимо кнопки.
        """
        platform = info.get("platform")
        value = info.get("value", "")
        try:
            if platform == "telegram":
                url = f"https://t.me/{value}"
                webbrowser.open(url)
                self._try_auto_click_call(contact_name, "Telegram", call_type)
            elif platform == "discord":
                url = f"https://discord.com/users/{value}"
                webbrowser.open(url)
                self._try_auto_click_call(contact_name, "Discord", call_type)
            else:
                self.update_log(f"⚠️ Неизвестная платформа контакта: {platform}")
        except Exception as e:
            self.update_log(f"⚠️ Ошибка звонка '{contact_name}': {e}")

    def _try_auto_click_call(self, contact_name, platform_label, call_type="voice"):
        """
        Общая логика авто-клика по звонку после открытия чата.

        Поддерживает два сценария, в зависимости от того, что
        откалибровано в CallButtonConfig:
        - Если откалибрована точка "menu" - сначала кликаем по ней
          (это открывает меню выбора обычный/видео звонок), затем,
          после небольшой паузы, кликаем по нужному пункту меню
          ("voice" или "video").
        - Если точка "menu" не откалибрована, но нужный тип звонка
          откалиброван напрямую - кликаем сразу по нему (подходит для
          интерфейсов без меню, где кнопки звонка и видеозвонка стоят
          отдельно).
        """
        call_label = "обычный звонок" if call_type == "voice" else "видеозвонок"

        if not AUTOSEND_AVALIBALE:
            self.update_log(f"📞 Открыт чат с {contact_name} в {platform_label} - "
                             f"нажмите {call_label} сами (модуль pyautogui не установлен)")
            self.speak(f"Открываю чат с {contact_name}. Нажмите {call_label} сами")
            return

        menu_pos = self.call_button.get_position("menu") if self.call_button else None
        target_pos = self.call_button.get_position(call_type) if self.call_button else None

        if not menu_pos and not target_pos:
            self.update_log(f"📞 Открыт чат с {contact_name} в {platform_label} - "
                             f"нажмите {call_label} сами (кнопки не откалиброваны, "
                             f"см. '📍 Откалибровать кнопки звонка')")
            self.speak(f"Открываю чат с {contact_name}. {call_label.capitalize()} не откалиброван, нажмите сами")
            return

        self.update_log(f"📞 Открыт чат с {contact_name} в {platform_label}, "
                         f"через {AUTOSEND_DELAY_SECONDS:.0f} сек попробую нажать {call_label}")
        self.speak(f"Открываю чат с {contact_name} и попробую сама включить {call_label}, "
                    f"не трогайте компьютер пару секунд")

        def _worker():
            time.sleep(AUTOSEND_DELAY_SECONDS)
            try:
                if menu_pos:
                    pyautogui.click(*menu_pos)
                    if target_pos:
                        time.sleep(0.7)  # Даём меню время открыться
                        pyautogui.click(*target_pos)
                        self.update_log(f"📞 Клик по кнопке звонка и пункту «{call_label}» выполнен "
                                         f"(проверьте, начался ли вызов)")
                    else:
                        self.update_log(f"📞 Нажал кнопку звонка, но пункт «{call_label}» не откалиброван - "
                                         f"выберите его в открывшемся меню сами")
                else:
                    pyautogui.click(*target_pos)
                    self.update_log(f"📞 Клик по кнопке ({call_label}) выполнен (проверьте, начался ли вызов)")
            except Exception as e:
                self.update_log(f"⚠️ Не удалось кликнуть по кнопке звонка: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def execute_write(self, contact_name, info, message):
        """
        Открывает чат контакта и подставляет продиктованное сообщение.

        ВАЖНО: ни Telegram, ни Discord не дают отправить сообщение
        по одной лишь ссылке (без официального бота/API с авторизацией):
        - Telegram (t.me/<user>?text=...) подставляет текст в поле ввода,
          но нажать "отправить" всё равно нужно самостоятельно.
        - Discord вообще не поддерживает подстановку текста через ссылку,
          поэтому мы копируем сообщение в буфер обмена - остаётся
          вставить (Ctrl+V) и отправить.
        """
        platform = info.get("platform")
        value = info.get("value", "")
        try:
            if platform == "telegram":
                url = f"https://t.me/{value}?text={quote(message)}"
                webbrowser.open(url)
                if AUTOSEND_AVALIBALE:
                    self.update_log(f"✍️ Сообщение для {contact_name} открыто в Telegram, отправлю через {AUTOSEND_DELAY_SECONDS:.0f} сек")
                    self.speak(f"Открываю чат с {contact_name} и отправлю сообщение сама, не трогайте компьютер пару секунд")
                    self._auto_send_enter()
                else:
                    self.update_log(f"✍️ Сообщение подготовлено для {contact_name} в Telegram")
                    self.speak(f"Сообщение для {contact_name} готово в чате. Отправьте его сами")
            elif platform == "discord":
                url = f"https://discord.com/users/{value}"
                webbrowser.open(url)
                if CLIPBOARD_AVALIBALE:
                    try:
                        pyperclip.copy(message)
                        self.update_log(f"✍️ Открыт чат с {contact_name}, сообщение скопировано в буфер обмена")
                        self.speak(f"Открыл чат с {contact_name} в Discord и скопировал сообщение. "
                                   f"Вставьте и отправьте сами")
                    except Exception:
                        self.update_log(f"✍️ Открыт чат с {contact_name}. Сообщение: {message}")
                        self.speak(f"Открыл чат с {contact_name}. Продиктованное сообщение: {message}")
                else:
                    self.update_log(f"✍️ Открыт чат с {contact_name}. Сообщение: {message}")
                    self.speak(f"Открыл чат с {contact_name}. Продиктованное сообщение: {message}")
            else:
                self.update_log(f"⚠️ Неизвестная платформа контакта: {platform}")
        except Exception as e:
            self.update_log(f"⚠️ Ошибка отправки сообщения '{contact_name}': {e}")

    def update_log(self, message):
        """
        Отправляет сообщение в интерфейс через callback.
        Используется для отображения статуса в логе приложения.
        """
        if self.callback:
            self.callback(message)

    def start(self, callback):
        """
        Запускает прослушивание микрофона.
        callback - функция для обновления лога в интерфейсе.
        Возвращает True при успешном запуске.
        """
        if not SPEECH_AVALIBALE:
            callback(f"❌ speech_recognition не загружен: {SPEECH_IMPORT_ERROR}")
            return False
        
        if not self.recognizer:
            callback("❌ Ошибка инициализации микрофона")
            return False
        
        # Устанавливаем callback
        self.is_listening = True
        self.callback = callback
        callback("🎤 Джарвис активирован!")
        self.speak("Джарвис активирован")
        
        # Запускаем цикл прослушивания в отдельном потоке
        threading.Thread(target=self.listen_loop, daemon=True).start()
        return True

    def stop(self):
        """
        Останавливает прослушивание микрофона.
        """
        self.is_listening = False
        self.is_active = False
        if self.callback:
            self.callback("⏹️ Джарвис деактивирован")                           

# ==================== ДИАЛОГ РЕДАКТИРОВАНИЯ ====================

class EditDialog(tk.Toplevel):
    """
    Окно для редактирования команды.
    Открывается при двойном клике по команде в списке.
    
    Позволяет изменить:
    1. Голосовую фразу
    2. Действие (путь к файлу или ссылку)
    """
    
    def __init__(self, parent, old_command, old_action, keyword="джарвис"):
        """
        Конструктор диалога.
        parent - родительское окно (главное приложение)
        old_command - текущая голосовая фраза
        old_action - текущее действие
        keyword - текущее активное ключевое слово (чтобы нельзя было
                   использовать его внутри самой команды)
        """
        super().__init__(parent)
        self.keyword = keyword.lower()
        self.title("✏️ Редактировать команду")
        self.resizable(False, False)
        self.result = None  # Сюда сохраняем результат редактирования
        
        # Делаем окно модальным (блокирует родительское окно)
        self.transient(parent)
        self.grab_set()
        
        # Создаём интерфейс
        self.create_widgets(old_command, old_action)
        
        # Подгоняем размер окна под содержимое и центрируем
        self.center_window()
        
        # Привязываем клавиши
        self.bind('<Return>', lambda e: self.save())  # Enter - сохранить
        self.bind('<Escape>', lambda e: self.cancel())  # Escape - отмена

    def center_window(self):
        """
        Подгоняет размер окна под реальное содержимое (чтобы ничего
        не обрезалось) и центрирует диалог относительно родительского окна.
        """
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = self.master.winfo_x() + (self.master.winfo_width() // 2) - (width // 2)
        y = self.master.winfo_y() + (self.master.winfo_height() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

    def create_widgets(self, old_command, old_action):
        """
        Создаёт виджеты диалога:
        1. Поле для голосовой фразы
        2. Поле для действия
        3. Кнопки "Сохранить" и "Отмена"
        """
        main_frame = tk.Frame(self, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ===== Поле "Голосовая команда" =====
        tk.Label(main_frame, text="🗣️ Голосовая команда:", 
                font=("Arial", 10)).grid(row=0, column=0, sticky=tk.W, pady=5)
        
        self.command_entry = tk.Entry(main_frame, width=40, font=("Arial", 10))
        self.command_entry.insert(0, old_command)  # Вставляем старую команду
        self.command_entry.grid(row=0, column=1, pady=5, padx=10)
        
        # ===== Поле "Действие" =====
        tk.Label(main_frame, text="⚡ Действие (путь/ссылка, несколько через ' | '):", 
                font=("Arial", 10)).grid(row=1, column=0, sticky=tk.W, pady=5)
        
        self.action_entry = tk.Entry(main_frame, width=40, font=("Arial", 10))
        self.action_entry.insert(0, old_action)  # Вставляем старое действие
        self.action_entry.grid(row=1, column=1, pady=5, padx=10)

        # Кнопка "Чат" (выбор чата в Telegram/Discord через ContactDialog)
        tk.Button(main_frame, text="💬 Чат", command=self.open_contact_dialog,
                 bg="#5865F2", fg="white", cursor="hand2")\
            .grid(row=1, column=2, padx=5, pady=5)
        
        # ===== Кнопки =====
        btn_frame = tk.Frame(main_frame)
        btn_frame.grid(row=2, column=0, columnspan=3, pady=20)
        
        # Кнопка "Сохранить" (зелёная)
        tk.Button(btn_frame, text="💾 Сохранить", command=self.save,
                 bg="#27ae60", fg="white", padx=30, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)
        
        # Кнопка "Отмена" (красная)
        tk.Button(btn_frame, text="❌ Отмена", command=self.cancel,
                 bg="#e74c3c", fg="white", padx=30, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)
        
        # Устанавливаем фокус на поле команды
        self.command_entry.focus()

    def save(self):
        """
        Сохраняет изменения.
        Проверяет, что поля не пустые и валидные.
        """
        command = self.command_entry.get().strip()
        action = self.action_entry.get().strip()
        
        # Проверка на пустые поля
        if not command or not action:
            messagebox.showwarning("Ошибка", "Заполните все поля!")
            return
        
        # Проверка на зарезервированное слово (текущее ключевое слово)
        if self.keyword in command.lower():
            messagebox.showwarning("Ошибка", f"Не используйте слово '{self.keyword}' в команде!")
            return
        
        # Сохраняем результат и закрываем окно
        self.result = (command, action)
        self.destroy()

    def cancel(self):
        """
        Отменяет редактирование.
        Закрывает окно без сохранения.
        """
        self.destroy()

    def open_contact_dialog(self):
        """
        Открывает диалог выбора чата (Telegram или Discord)
        и добавляет полученную ссылку в поле "Действие".
        """
        dialog = ContactDialog(self)
        self.wait_window(dialog)

        if dialog.result:
            url = dialog.result
            current = self.action_entry.get().strip()
            if current:
                self.action_entry.insert(tk.END, f" | {url}")
            else:
                self.action_entry.insert(0, url)


class ContactDialog(tk.Toplevel):
    """
    Окно для создания действия "открыть чат с человеком"
    в Telegram или Discord.

    Пользователь выбирает платформу и вводит идентификатор
    (username для Telegram, числовой ID для Discord), диалог
    собирает готовую ссылку (обычную https-ссылку, поэтому она
    открывается уже существующим механизмом обработки веб-ссылок
    в VoiceEngine.execute_action - никаких доп. правок движка
    не требуется).

    Результат (self.result) - готовая ссылка, которую можно
    вставить в поле "Действие", в том числе вперемешку с другими
    действиями через " | ".
    """

    # Подсказки под полем ввода - меняются в зависимости от выбранной платформы
    PLATFORM_HINTS = {
        "telegram": (
            "Укажите username пользователя в Telegram (без @), например: durov.\n"
            "Ссылка откроет чат напрямую в приложении Telegram, если оно "
            "установлено и связано с t.me-ссылками, иначе - в веб-версии."
        ),
        "discord_profile": (
            "Укажите ID пользователя Discord (числовой).\n"
            "Как узнать ID: Настройки Discord → Расширенные → включить "
            "'Режим разработчика', затем правой кнопкой по пользователю → "
            "'Копировать ID пользователя'.\n"
            "Ссылка откроет профиль пользователя, дальше нужно нажать "
            "'Написать сообщение'."
        ),
        "discord_dm": (
            "Укажите ID диалога (личной переписки) Discord.\n"
            "Как узнать ID: включите 'Режим разработчика' (см. выше), "
            "затем правой кнопкой по нужному диалогу в списке слева → "
            "'Копировать ID'.\n"
            "Ссылка откроет сразу нужный чат, без лишних кликов."
        ),
    }

    def __init__(self, parent):
        """
        Конструктор диалога.
        parent - родительское окно (главное приложение)
        """
        super().__init__(parent)
        self.title("💬 Открыть чат с человеком")
        self.resizable(False, False)
        self.result = None  # Сюда сохраняем готовую ссылку

        # Делаем окно модальным
        self.transient(parent)
        self.grab_set()

        self.platform_var = tk.StringVar(value="telegram")

        self.create_widgets()
        self.center_window()

        self.bind('<Return>', lambda e: self.save())
        self.bind('<Escape>', lambda e: self.cancel())

    def center_window(self):
        """
        Подгоняет размер окна под реальное содержимое (чтобы ничего
        не обрезалось) и центрирует диалог относительно родительского окна.
        """
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = self.master.winfo_x() + (self.master.winfo_width() // 2) - (width // 2)
        y = self.master.winfo_y() + (self.master.winfo_height() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

    def create_widgets(self):
        """
        Создаёт виджеты диалога:
        1. Выбор платформы (Telegram / Discord-профиль / Discord-диалог)
        2. Поле для идентификатора (username или ID)
        3. Подсказка, объясняющая где взять идентификатор
        4. Кнопки "Добавить" и "Отмена"
        """
        main_frame = tk.Frame(self, padx=20, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Платформа:", font=("Arial", 10, "bold"))\
            .grid(row=0, column=0, sticky=tk.W, pady=(0, 5))

        platform_frame = tk.Frame(main_frame)
        platform_frame.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        tk.Radiobutton(platform_frame, text="✈️ Telegram", variable=self.platform_var,
                       value="telegram", command=self.update_hint,
                       font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 15))
        tk.Radiobutton(platform_frame, text="🎮 Discord (профиль)", variable=self.platform_var,
                       value="discord_profile", command=self.update_hint,
                       font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 15))
        tk.Radiobutton(platform_frame, text="🎮 Discord (диалог)", variable=self.platform_var,
                       value="discord_dm", command=self.update_hint,
                       font=("Arial", 9)).pack(side=tk.LEFT)

        tk.Label(main_frame, text="Идентификатор:", font=("Arial", 10))\
            .grid(row=2, column=0, sticky=tk.W, pady=5)
        self.id_entry = tk.Entry(main_frame, width=35, font=("Arial", 10))
        self.id_entry.grid(row=2, column=1, sticky=tk.W, pady=5, padx=10)

        self.hint_label = tk.Label(main_frame, text="", font=("Arial", 8),
                                   fg="gray", justify=tk.LEFT, wraplength=490)
        self.hint_label.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(5, 15))

        btn_frame = tk.Frame(main_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)

        # Кнопка "Добавить" (зелёная)
        tk.Button(btn_frame, text="✅ Добавить", command=self.save,
                 bg="#27ae60", fg="white", padx=25, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)

        # Кнопка "Отмена" (красная)
        tk.Button(btn_frame, text="❌ Отмена", command=self.cancel,
                 bg="#e74c3c", fg="white", padx=25, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)

        self.update_hint()
        self.id_entry.focus()

    def update_hint(self):
        """
        Обновляет текст подсказки под полем ввода
        в зависимости от выбранной платформы.
        """
        self.hint_label.config(text=self.PLATFORM_HINTS[self.platform_var.get()])

    def save(self):
        """
        Собирает ссылку на основе выбранной платформы и введённого
        идентификатора, проверяет корректность и сохраняет результат.
        """
        value = self.id_entry.get().strip()
        if not value:
            messagebox.showwarning("Ошибка", "Введите идентификатор!")
            return

        platform = self.platform_var.get()

        if platform == "telegram":
            username = value.lstrip("@").strip()
            if not username:
                messagebox.showwarning("Ошибка", "Введите username в Telegram!")
                return
            url = f"https://t.me/{username}"
        else:
            # Discord ID - только цифры (на случай, если пользователь
            # случайно скопировал что-то с лишними символами)
            user_id = re.sub(r'\D', '', value)
            if not user_id:
                messagebox.showwarning("Ошибка", "ID Discord должен состоять из цифр!")
                return
            if platform == "discord_profile":
                url = f"https://discord.com/users/{user_id}"
            else:  # discord_dm
                url = f"https://discord.com/channels/@me/{user_id}"

        self.result = url
        self.destroy()

    def cancel(self):
        """
        Отменяет создание ссылки.
        Закрывает окно без сохранения.
        """
        self.destroy()


class ContactEntryDialog(tk.Toplevel):
    """
    Окно добавления/редактирования контакта в словаре контактов
    (используется командами "напиши ..." и "позвони ...").

    В отличие от ContactDialog (которая просто собирает готовую
    ссылку для поля "Действие"), этот диалог сохраняет контакт
    по имени в contacts.json, чтобы потом искать его по голосу.
    """

    def __init__(self, parent, name="", platform="telegram", value="", aliases=""):
        super().__init__(parent)
        self.title("👥 Контакт")
        self.resizable(False, False)
        self.result = None  # (name, platform, value, aliases_list)

        self.transient(parent)
        self.grab_set()

        self.platform_var = tk.StringVar(value=platform)

        self.create_widgets(name, value, aliases)
        self.center_window()

        self.bind('<Return>', lambda e: self.save())
        self.bind('<Escape>', lambda e: self.cancel())

    def center_window(self):
        """
        Подгоняет размер окна под реальное содержимое (чтобы ничего
        не обрезалось) и центрирует диалог относительно родительского окна.
        """
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = self.master.winfo_x() + (self.master.winfo_width() // 2) - (width // 2)
        y = self.master.winfo_y() + (self.master.winfo_height() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

    def create_widgets(self, name, value, aliases):
        main_frame = tk.Frame(self, padx=20, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Имя (как обращаться голосом):", font=("Arial", 10))\
            .grid(row=0, column=0, sticky=tk.W, pady=5)
        self.name_entry = tk.Entry(main_frame, width=35, font=("Arial", 10))
        self.name_entry.insert(0, name)
        self.name_entry.grid(row=0, column=1, pady=5, padx=10)

        tk.Label(main_frame, text="Платформа:", font=("Arial", 10, "bold"))\
            .grid(row=1, column=0, sticky=tk.W, pady=(10, 5))
        platform_frame = tk.Frame(main_frame)
        platform_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W)
        tk.Radiobutton(platform_frame, text="✈️ Telegram", variable=self.platform_var,
                       value="telegram", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 15))
        tk.Radiobutton(platform_frame, text="🎮 Discord", variable=self.platform_var,
                       value="discord", font=("Arial", 9)).pack(side=tk.LEFT)

        tk.Label(main_frame, text="Username (Telegram) или ID (Discord):", font=("Arial", 10))\
            .grid(row=3, column=0, sticky=tk.W, pady=(10, 5))
        self.value_entry = tk.Entry(main_frame, width=35, font=("Arial", 10))
        self.value_entry.insert(0, value)
        self.value_entry.grid(row=3, column=1, pady=5, padx=10)

        tk.Label(main_frame, text="Другие словоформы имени, через запятую\n(например: маме, маму - если основное имя 'мама'):",
                font=("Arial", 9), justify=tk.LEFT)\
            .grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 5))
        self.aliases_entry = tk.Entry(main_frame, width=45, font=("Arial", 10))
        self.aliases_entry.insert(0, aliases)
        self.aliases_entry.grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=0)

        btn_frame = tk.Frame(main_frame)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=20)
        tk.Button(btn_frame, text="💾 Сохранить", command=self.save,
                 bg="#27ae60", fg="white", padx=30, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="❌ Отмена", command=self.cancel,
                 bg="#e74c3c", fg="white", padx=30, pady=8,
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)

        self.name_entry.focus()

    def save(self):
        name = self.name_entry.get().strip().lower()
        value = self.value_entry.get().strip()
        platform = self.platform_var.get()
        aliases_raw = self.aliases_entry.get().strip()
        aliases = [a.strip().lower() for a in aliases_raw.split(",") if a.strip()]

        if not name or not value:
            messagebox.showwarning("Ошибка", "Укажите имя и Username/ID!")
            return

        if platform == "discord":
            digits = re.sub(r'\D', '', value)
            if not digits:
                messagebox.showwarning("Ошибка", "ID Discord должен состоять из цифр!")
                return
            value = digits
        else:
            value = value.lstrip("@").strip()
            if not value:
                messagebox.showwarning("Ошибка", "Введите username в Telegram!")
                return

        self.result = (name, platform, value, aliases)
        self.destroy()

    def cancel(self):
        self.destroy()


# ==================== ГЛАВНОЕ ПРИЛОЖЕНИЕ ====================

class VoiceAssistantApp:
    """
    Главное окно приложения.
    Содержит:
    1. Верхняя панель с заголовком
    2. Панель управления (вкл/выкл, статус, индикатор)
    3. Панель добавления команд
    4. Список всех команд
    5. Кнопки управления командами
    6. Лог статуса внизу
    """
    
    def __init__(self, root):
        """
        Конструктор главного приложения.
        root - корневой объект Tkinter (главное окно)
        """
        self.root = root
        self.root.title("🤖 Джарвис - Голосовой Ассистент")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)  # Минимальный размер
        
        # ===== Попытка загрузить иконку =====
        try:
            icon_path = get_resource_path("icon.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except:
            pass
        
        # ===== Инициализация менеджеров =====
        self.manager = CommandManager()  # Управление командами
        self.contacts = ContactManager()  # Управление контактами (для "напиши"/"позвони")
        self.call_button = CallButtonConfig()  # Калибровка кнопки звонка
        self.voice = VoiceEngine(self.manager, self.contacts, self.call_button)  # Голосовой движок
        # Позволяет голосовой команде "закройся"/"выключись" закрыть всё
        # приложение. root.after используется, чтобы закрытие окна Tkinter
        # выполнялось из основного потока (voice-поток - фоновый).
        self.voice.exit_callback = lambda: self.root.after(0, self.on_closing)
        
        # ===== Создание интерфейса =====
        self.create_widgets()
        
        # ===== Загрузка и отображение команд =====
        self.refresh_list()
        self.refresh_contacts_list()
        
        # ===== Установка начального статуса =====
        self.update_status("⚪ Остановлен", "gray")
        self.update_log(f"👋 Готов к работе! Скажите '{self.voice.keyword.capitalize()}, открой браузер'")
        
        # ===== Обработка закрытия окна =====
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ==================== СОЗДАНИЕ ИНТЕРФЕЙСА ====================

    def create_widgets(self):
        """
        Создаёт все виджеты приложения.
        Разбито на логические блоки:
        1. Заголовок
        2. Панель управления
        3. Панель добавления команд
        4. Список команд
        5. Лог статуса
        """
        
        # ===== 1. Верхняя панель (заголовок) =====
        header_frame = tk.Frame(self.root, bg="#2c3e50", height=80)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)
        
        # Название приложения
        tk.Label(header_frame, text="🤖 ДЖАРВИС", 
                font=("Arial", 24, "bold"),
                bg="#2c3e50", fg="#ecf0f1").pack(pady=5)
        
        # Подзаголовок
        tk.Label(header_frame, text="Скажите 'Джарвис, ...' чтобы активировать",
                font=("Arial", 10),
                bg="#2c3e50", fg="#bdc3c7").pack()

        # ===== 2. Панель управления =====
        control_frame = tk.Frame(self.root, bg="#ecf0f1", height=70)
        control_frame.pack(fill=tk.X, padx=10, pady=5)
        control_frame.pack_propagate(False)
        
        # Кнопка включения/выключения
        self.listen_btn = tk.Button(control_frame, text="🎤 Включить Джарвиса",
                                   command=self.toggle_listening,
                                   font=("Arial", 12, "bold"),
                                   bg="#27ae60", fg="white",
                                   padx=25, pady=8, cursor="hand2")
        self.listen_btn.pack(side=tk.LEFT, padx=10)
        
        # Статус
        self.status_label = tk.Label(control_frame, text="⚪ Остановлен",
                                    font=("Arial", 12, "bold"),
                                    bg="#ecf0f1")
        self.status_label.pack(side=tk.LEFT, padx=20)
        
        # Индикатор (красный/зелёный)
        self.indicator_label = tk.Label(control_frame, text="🔴",
                                       font=("Arial", 16),
                                       bg="#ecf0f1")
        self.indicator_label.pack(side=tk.LEFT, padx=10)
        
        # Подсказка
        tk.Label(control_frame, text="💡 Двойной клик по команде для редактирования",
                font=("Arial", 9), bg="#ecf0f1", fg="#7f8c8d").pack(side=tk.RIGHT, padx=10)

        # ===== 2.1 Панель настройки ключевого слова =====
        keyword_frame = tk.Frame(self.root, bg="#ecf0f1")
        keyword_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        tk.Label(keyword_frame, text="🔑 Ключевое слово:", font=("Arial", 10),
                bg="#ecf0f1").pack(side=tk.LEFT, padx=(10, 5))

        self.keyword_entry = tk.Entry(keyword_frame, width=15, font=("Arial", 10))
        self.keyword_entry.insert(0, self.voice.keyword)
        self.keyword_entry.pack(side=tk.LEFT, padx=5)

        tk.Button(keyword_frame, text="💾 Сохранить слово", command=self.save_keyword,
                 bg="#8e44ad", fg="white", cursor="hand2", padx=10)\
            .pack(side=tk.LEFT, padx=5)

        tk.Label(keyword_frame, text="(например: вова, дима — вместо 'джарвис')",
                font=("Arial", 8), fg="gray", bg="#ecf0f1").pack(side=tk.LEFT, padx=5)

        # ===== 3. Панель добавления команды =====
        add_frame = tk.LabelFrame(self.root, text="➕ Добавить команду",
                                 font=("Arial", 10, "bold"),
                                 bg="#f8f9fa", padx=10, pady=10)
        add_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Поле "Голосовая команда"
        tk.Label(add_frame, text="🗣️ Команда:", font=("Arial", 9), 
                bg="#f8f9fa").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.command_entry = tk.Entry(add_frame, width=25, font=("Arial", 10))
        self.command_entry.grid(row=0, column=1, padx=5)
        
        # Подсказка под полем
        tk.Label(add_frame, text="(например: 'открой браузер')",
                font=("Arial", 8), fg="gray", bg="#f8f9fa").grid(row=0, column=2, sticky=tk.W)
        
        # Поле "Действие"
        tk.Label(add_frame, text="⚡ Действие:", font=("Arial", 9),
                bg="#f8f9fa").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.action_entry = tk.Entry(add_frame, width=40, font=("Arial", 10))
        self.action_entry.grid(row=1, column=1, padx=5, pady=5, columnspan=2)
        
        # Подсказка про несколько приложений
        tk.Label(add_frame, text="(несколько приложений через ' | ', например: chrome.exe | notepad.exe)",
                font=("Arial", 8), fg="gray", bg="#f8f9fa").grid(row=2, column=0, columnspan=3, sticky=tk.W, padx=5)
        
        # Кнопка "Обзор" (выбор файла через проводник)
        tk.Button(add_frame, text="📂 Обзор", command=self.browse_file,
                 bg="#3498db", fg="white", cursor="hand2")\
            .grid(row=1, column=3, padx=5, pady=5)

        # Кнопка "Чат" (выбор чата в Telegram/Discord через ContactDialog)
        tk.Button(add_frame, text="💬 Чат", command=self.open_contact_dialog,
                 bg="#5865F2", fg="white", cursor="hand2")\
            .grid(row=1, column=4, padx=5, pady=5)
        
        # Кнопка "Добавить команду"
        tk.Button(add_frame, text="✅ Добавить команду", command=self.add_command,
                 bg="#27ae60", fg="white", padx=20, pady=5,
                 font=("Arial", 10, "bold"), cursor="hand2")\
            .grid(row=3, column=0, columnspan=4, pady=10)

        # ===== 3.1 Панель контактов (для "напиши"/"позвони") =====
        contacts_frame = tk.LabelFrame(self.root, text="👥 Контакты (для команд \"напиши\" / \"позвони\")",
                                      font=("Arial", 10, "bold"),
                                      bg="#f8f9fa", padx=10, pady=10)
        contacts_frame.pack(fill=tk.X, padx=10, pady=5)

        contacts_columns = ("Имя", "Платформа", "ID/Username", "Словоформы")
        self.contacts_tree = ttk.Treeview(contacts_frame, columns=contacts_columns, show="headings",
                                          height=4, style="Jarvis.Treeview")
        self.contacts_tree.heading("Имя", text="🗣️ Имя")
        self.contacts_tree.heading("Платформа", text="Платформа")
        self.contacts_tree.heading("ID/Username", text="ID / Username")
        self.contacts_tree.heading("Словоформы", text="Другие словоформы")
        self.contacts_tree.column("Имя", width=120)
        self.contacts_tree.column("Платформа", width=90)
        self.contacts_tree.column("ID/Username", width=180)
        self.contacts_tree.column("Словоформы", width=200)
        self.contacts_tree.pack(fill=tk.X)
        self.contacts_tree.bind('<Double-Button-1>', lambda e: self.edit_contact())

        contacts_btn_frame = tk.Frame(contacts_frame, bg="#f8f9fa")
        contacts_btn_frame.pack(fill=tk.X, pady=5)

        tk.Button(contacts_btn_frame, text="➕ Добавить контакт", command=self.add_contact,
                 bg="#27ae60", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)
        tk.Button(contacts_btn_frame, text="✏️ Редактировать", command=self.edit_contact,
                 bg="#f39c12", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)
        tk.Button(contacts_btn_frame, text="🗑️ Удалить", command=self.delete_contact,
                 bg="#e74c3c", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)
        tk.Button(contacts_btn_frame, text="📍 Откалибровать кнопки звонка", command=self.calibrate_call_button,
                 bg="#8e44ad", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)

        tk.Label(contacts_frame,
                text="Пример: 'Джарвис, напиши маме привет как дела', 'Джарвис, позвони паше', "
                     "'Джарвис, видеозвони маме', 'Джарвис, сбрось звонок', 'Джарвис, закрой' "
                     "(закрывает текущее активное окно), 'Джарвис, закройся' (закрывает сам Джарвис) "
                     "или 'Джарвис, загугли рецепт борща' (ищет в Google). "
                     "Для авто-клика по звонку сначала откалибруйте кнопки "
                     "(окно Telegram держите развёрнутым на весь экран в одном и том же положении).",
                font=("Arial", 8), fg="gray", bg="#f8f9fa", wraplength=820, justify=tk.LEFT).pack(anchor=tk.W)

        # ===== 4. Список команд (таблица) =====
        list_frame = tk.LabelFrame(self.root, text="📋 Мои команды",
                                  font=("Arial", 10, "bold"),
                                  bg="#f8f9fa", padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Создаём таблицу с двумя колонками
        columns = ("Команда", "Действие")

        # ===== Явная настройка стиля таблицы =====
        # По умолчанию ttk.Treeview на некоторых системах (особенно
        # при масштабировании экрана) занижает высоту строки относительно
        # реального размера шрифта — из-за этого текст соседних строк
        # накладывается друг на друга. Задаём высоту строки и шрифт
        # вручную, независимо от системного масштабирования.
        style = ttk.Style()
        style.configure("Jarvis.Treeview",
                         rowheight=28,
                         font=("Arial", 10))
        style.configure("Jarvis.Treeview.Heading",
                         font=("Arial", 10, "bold"))

        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings",
                                  height=8, style="Jarvis.Treeview")
        self.tree.heading("Команда", text="🗣️ Что говорить")
        self.tree.heading("Действие", text="⚡ Что открывается")
        self.tree.column("Команда", width=200)
        self.tree.column("Действие", width=530)
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        # Привязываем двойной клик для редактирования
        self.tree.bind('<Double-Button-1>', lambda e: self.edit_selected())
        
        # Панель кнопок управления списком
        btn_frame = tk.Frame(list_frame, bg="#f8f9fa")
        btn_frame.pack(fill=tk.X, pady=5)
        
        # Кнопки управления
        tk.Button(btn_frame, text="✏️ Редактировать", command=self.edit_selected,
                 bg="#f39c12", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)
        
        tk.Button(btn_frame, text="🗑️ Удалить", command=self.delete_command,
                 bg="#e74c3c", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)
        
        tk.Button(btn_frame, text="🧹 Очистить все", command=self.clear_all,
                 bg="#95a5a6", fg="white", padx=10, cursor="hand2").pack(side=tk.LEFT, padx=2)

        # ===== 5. Лог статуса (нижняя панель) =====
        log_frame = tk.Frame(self.root, bg="#34495e", height=50)
        log_frame.pack(fill=tk.X, side=tk.BOTTOM)
        log_frame.pack_propagate(False)
        
        self.log_label = tk.Label(log_frame, text="💡 Готов к работе",
                                 font=("Arial", 10),
                                 bg="#34495e", fg="#ecf0f1")
        self.log_label.pack(pady=12)

    # ==================== МЕТОДЫ ПРИЛОЖЕНИЯ ====================

    def save_keyword(self):
        """
        Сохраняет новое ключевое слово активации (например, вместо
        'джарвис' можно поставить 'вова' или 'дима').
        Слово используется сразу же, без перезапуска приложения.
        """
        new_keyword = self.keyword_entry.get().strip().lower()

        if not new_keyword:
            messagebox.showwarning("Ошибка", "Введите ключевое слово!")
            return

        if len(new_keyword) < 2:
            messagebox.showwarning("Ошибка", "Слово слишком короткое!")
            return

        if self.voice.save_keyword(new_keyword):
            self.update_log(f"✅ Ключевое слово изменено на: '{new_keyword}'")
            messagebox.showinfo("Готово", f"Теперь для активации говорите: '{new_keyword}'")
        else:
            messagebox.showerror("Ошибка", "Не удалось сохранить ключевое слово!")

    def browse_file(self):
        """
        Открывает диалог выбора файла.
        Добавляет выбранный путь в поле "Действие".
        Если в поле уже что-то есть - дописывает через " | ",
        чтобы можно было собрать несколько приложений в одной команде.
        """
        file_path = filedialog.askopenfilename(
            title="Выберите файл или программу",
            filetypes=[
                ("Все файлы", "*.*"),
                ("Исполняемые", "*.exe"),
                ("Документы", "*.docx;*.pdf;*.txt"),
                ("Медиа", "*.mp3;*.mp4;*.jpg;*.png")
            ]
        )
        if file_path:
            current = self.action_entry.get().strip()
            if current:
                self.action_entry.insert(tk.END, f" | {file_path}")
            else:
                self.action_entry.insert(0, file_path)

    def open_contact_dialog(self):
        """
        Открывает диалог выбора чата (Telegram или Discord).
        Полученная ссылка добавляется в поле "Действие" -
        так же, как путь к файлу через кнопку "Обзор":
        если поле уже не пустое, ссылка дописывается через " | ",
        чтобы её можно было совместить с другими действиями.
        """
        dialog = ContactDialog(self.root)
        self.root.wait_window(dialog)  # Ждём закрытия диалога

        if dialog.result:
            url = dialog.result
            current = self.action_entry.get().strip()
            if current:
                self.action_entry.insert(tk.END, f" | {url}")
            else:
                self.action_entry.insert(0, url)

    def add_command(self):
        """
        Добавляет новую команду из полей ввода.
        Проверяет:
        1. Поля не пустые
        2. Команда не содержит "джарвис"
        3. Заменяет существующую команду при подтверждении
        """
        command = self.command_entry.get().strip()
        action = self.action_entry.get().strip()
        
        # Проверка на пустые поля
        if not command or not action:
            messagebox.showwarning("Ошибка", "Введите все данные!")
            return
        
        # Проверка на зарезервированное слово (текущее ключевое слово)
        if self.voice.keyword in command.lower():
            messagebox.showwarning("Ошибка", f"Не используйте слово '{self.voice.keyword}' в команде!")
            return
        
        # Проверка на существующую команду
        if command in self.manager.commands:
            if not messagebox.askyesno("Подтверждение", 
                                       f"Команда '{command}' уже существует. Заменить?"):
                return
        
        # Добавляем команду
        if self.manager.add(command, action):
            self.refresh_list()
            self.command_entry.delete(0, tk.END)
            self.action_entry.delete(0, tk.END)
            self.update_log(f"✅ Команда '{command}' добавлена")
        else:
            messagebox.showerror("Ошибка", "Не удалось сохранить команду!")

    def edit_selected(self):
        """
        Открывает диалог редактирования для выбранной команды.
        """
        # Получаем выбранную команду
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите команду для редактирования!")
            return
        
        # Извлекаем данные
        item = selected[0]
        values = self.tree.item(item)['values']
        old_command = values[0]
        old_action = self.manager.commands[old_command]
        
        # Открываем диалог
        dialog = EditDialog(self.root, old_command, old_action, self.voice.keyword)
        self.root.wait_window(dialog)  # Ждём закрытия диалога
        
        # Применяем изменения
        if dialog.result:
            new_command, new_action = dialog.result
            if self.manager.edit(old_command, new_command, new_action):
                self.refresh_list()
                self.update_log(f"✏️ Команда '{old_command}' обновлена")

    def delete_command(self):
        """
        Удаляет выбранные команды из списка.
        """
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите команду для удаления!")
            return
        
        # Подтверждение удаления
        if messagebox.askyesno("Подтверждение", "Удалить выбранные команды?"):
            for item in selected:
                command = self.tree.item(item)['values'][0]
                self.manager.delete(command)
            self.refresh_list()
            self.update_log("🗑️ Команды удалены")

    def clear_all(self):
        """
        Удаляет все команды из списка.
        """
        if not self.manager.commands:
            messagebox.showinfo("Инфо", "Список команд пуст")
            return
        
        if messagebox.askyesno("Подтверждение", "Удалить все команды?"):
            self.manager.commands = {}
            self.manager.save()
            self.refresh_list()
            self.update_log("🧹 Все команды удалены")

    def refresh_list(self):
        """
        Обновляет отображение списка команд.
        """
        # Очищаем таблицу
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Заполняем таблицу
        for command, action in self.manager.get_all().items():
            # Обрезаем длинные пути для красоты
            display_action = action if len(action) < 60 else action[:57] + "..."
            self.tree.insert("", tk.END, values=(command, display_action))

    # ==================== УПРАВЛЕНИЕ КОНТАКТАМИ ====================

    def add_contact(self):
        """
        Открывает диалог добавления нового контакта.
        """
        dialog = ContactEntryDialog(self.root)
        self.root.wait_window(dialog)

        if dialog.result:
            name, platform, value, aliases = dialog.result
            if name in self.contacts.contacts:
                if not messagebox.askyesno("Подтверждение",
                                           f"Контакт '{name}' уже существует. Заменить?"):
                    return
            if self.contacts.add(name, platform, value, aliases):
                self.refresh_contacts_list()
                self.update_log(f"✅ Контакт '{name}' добавлен")
            else:
                messagebox.showerror("Ошибка", "Не удалось сохранить контакт!")

    def edit_contact(self):
        """
        Открывает диалог редактирования выбранного контакта.
        """
        selected = self.contacts_tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите контакт для редактирования!")
            return

        item = selected[0]
        old_name = self.contacts_tree.item(item)['values'][0]
        info = self.contacts.contacts.get(old_name, {})

        dialog = ContactEntryDialog(
            self.root,
            name=old_name,
            platform=info.get("platform", "telegram"),
            value=info.get("value", ""),
            aliases=", ".join(info.get("aliases", []))
        )
        self.root.wait_window(dialog)

        if dialog.result:
            new_name, platform, value, aliases = dialog.result
            if self.contacts.edit(old_name, new_name, platform, value, aliases):
                self.refresh_contacts_list()
                self.update_log(f"✏️ Контакт '{old_name}' обновлён")

    def delete_contact(self):
        """
        Удаляет выбранный контакт.
        """
        selected = self.contacts_tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите контакт для удаления!")
            return

        if messagebox.askyesno("Подтверждение", "Удалить выбранные контакты?"):
            for item in selected:
                name = self.contacts_tree.item(item)['values'][0]
                self.contacts.delete(name)
            self.refresh_contacts_list()
            self.update_log("🗑️ Контакты удалены")

    def calibrate_call_button(self):
        """
        Запускает калибровку координат кнопок звонка в два этапа:
        сначала обычный (голосовой) звонок, затем видеозвонок. На
        каждом этапе показывается окно с обратным отсчётом, за это
        время пользователь должен сам открыть чат с любым контактом
        в Telegram (окно - на весь экран) и навести курсор точно на
        нужную кнопку. По истечении отсчёта текущая позиция курсора
        сохраняется и используется дальше для авто-клика при командах
        "позвони" и "видеозвони".

        ВАЖНО: это привязка к фиксированным координатам экрана, а не
        распознавание кнопки. Если потом окно Telegram будет открываться
        в другом месте/размере - клик попадёт мимо, и калибровку
        нужно будет повторить.
        """
        if not AUTOSEND_AVALIBALE:
            messagebox.showerror(
                "Недоступно",
                "Для калибровки и авто-клика нужен модуль pyautogui.\n"
                "Установите его (pip install pyautogui) и пересоберите приложение."
            )
            return

        stages = [
            ("menu", "кнопку звонка (ту, что открывает меню выбора обычный/видео)"),
            ("voice", "пункт «Обычный звонок» в открывшемся меню"),
            ("video", "пункт «Видеозвонок» в открывшемся меню"),
            ("hangup", "кнопку сброса/завершения звонка (видна во время активного звонка)"),
        ]

        win = tk.Toplevel(self.root)
        win.title("📍 Калибровка кнопок звонка")
        win.resizable(False, False)
        win.attributes('-topmost', True)  # Остаётся видимым, даже когда открыт Telegram
        win.transient(self.root)

        label = tk.Label(win, font=("Arial", 11), padx=20, pady=20,
                         justify=tk.LEFT, wraplength=380)
        label.pack()

        stage_index = [0]
        seconds_left = [5]

        def run_stage():
            call_type, call_name = stages[stage_index[0]]
            seconds_left[0] = 5
            update_countdown(call_type, call_name)

        def update_countdown(call_type, call_name):
            if seconds_left[0] <= 0:
                x, y = pyautogui.position()
                self.call_button.set_position(x, y, call_type=call_type)
                self.update_log(f"📍 Кнопка {call_name} откалибрована: ({x}, {y})")

                stage_index[0] += 1
                if stage_index[0] < len(stages):
                    run_stage()
                else:
                    win.destroy()
                    messagebox.showinfo("Готово", "Координаты обеих кнопок звонка сохранены.")
                return

            label.config(text=(
                f"Этап {stage_index[0] + 1} из {len(stages)}.\n\n"
                f"Разверните Telegram на весь экран, откройте любой чат.\n"
                f"На 1-м этапе просто наведите курсор на кнопку звонка (не нажимая).\n"
                f"На 2-м и 3-м этапах, после того как нажмёте кнопку звонка сами и "
                f"откроется меню выбора - наведите курсор на нужный пункт меню.\n"
                f"На 4-м этапе позвоните сами (или примите звонок) и, пока идёт вызов, "
                f"наведите курсор на кнопку сброса.\n\n"
                f"Сейчас наведите курсор на: {call_name}\n\n"
                f"Захват координат курсора через: {seconds_left[0]} сек"
            ))
            seconds_left[0] -= 1
            win.after(1000, lambda: update_countdown(call_type, call_name))

        run_stage()

    def refresh_contacts_list(self):
        """
        Обновляет отображение списка контактов.
        """
        for item in self.contacts_tree.get_children():
            self.contacts_tree.delete(item)

        for name, info in self.contacts.get_all().items():
            aliases = ", ".join(info.get("aliases", []))
            self.contacts_tree.insert("", tk.END, values=(
                name, info.get("platform", ""), info.get("value", ""), aliases
            ))

    def toggle_listening(self):
        """
        Включает или выключает прослушивание микрофона.
        Меняет внешний вид кнопки и индикатора.
        """
        if not self.voice.is_listening:
            # Включаем
            if self.voice.start(self.update_log):
                self.listen_btn.config(text="⏹️ Выключить Джарвиса", bg="#e74c3c")
                self.update_status("🟢 Активен", "#27ae60")
                self.indicator_label.config(text="🟢")
        else:
            # Выключаем
            self.voice.stop()
            self.listen_btn.config(text="🎤 Включить Джарвиса", bg="#27ae60")
            self.update_status("⚪ Остановлен", "gray")
            self.indicator_label.config(text="🔴")

    def update_status(self, text, color):
        """
        Обновляет статус в панели управления.
        """
        self.status_label.config(text=text, foreground=color)

    def update_log(self, message):
        """
        Обновляет лог в нижней панели.
        Меняет цвет в зависимости от типа сообщения.
        """
        self.log_label.config(text=message)
        
        if "✅" in message or "🔊" in message:
            self.log_label.config(foreground="#2ecc71")  # Зелёный - успех
        elif "❌" in message or "⚠️" in message:
            self.log_label.config(foreground="#e74c3c")  # Красный - ошибка
        elif "ℹ️" in message:
            self.log_label.config(foreground="#3498db")  # Синий - информация
        else:
            self.log_label.config(foreground="#ecf0f1")  # Белый - обычный

    def on_closing(self):
        """
        Обработчик закрытия окна.
        Останавливает прослушивание перед выходом.
        """
        if self.voice.is_listening:
            self.voice.stop()
        self.root.destroy()

# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================

if __name__ == "__main__":
    """
    Точка входа в программу.
    Создаёт корневое окно Tkinter и запускает приложение.
    """
    # Включаем поддержку High-DPI на Windows, чтобы интерфейс
    # не масштабировался "по-пиксельному" (размыто/зубчато) на экранах
    # с масштабом больше 100%. Без этого Windows растягивает уже
    # отрисованное окно как картинку, отсюда и пиксельные края кнопок.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # Создаём корневое окно
    root = tk.Tk()

    # ===== Синхронизация масштаба Tk с реальным DPI Windows =====
    # SetProcessDpiAwareness делает окно "чётким" на экранах с масштабом
    # >100%, но сам Tkinter (и, в частности, ttk.Treeview) не знает
    # об этом масштабе и продолжает считать размеры строк/шрифтов так,
    # будто DPI = 96. Из-за этого текст в таблице команд рисуется крупнее,
    # чем высота строки, и соседние строки визуально накладываются друг
    # на друга. Явно сообщаем Tk актуальный масштаб экрана, чтобы все
    # виджеты (включая Treeview) считали размеры правильно.
    if sys.platform == "win32":
        try:
            dpi = root.winfo_fpixels('1i')  # физических пикселей на дюйм
            scaling = dpi / 72.0
            root.tk.call('tk', 'scaling', scaling)
        except Exception:
            pass
    
    # Создаём экземпляр приложения
    app = VoiceAssistantApp(root)

    # ===== Разворачиваем окно на весь экран =====
    # Используем нативную максимизацию окна (не ручное растягивание
    # geometry на размер экрана), чтобы Windows не подгоняла картинку
    # искусственно и не терялась чёткость - за это отвечает уже
    # включённый DPI awareness выше, окно просто разворачивается
    # в свой настоящий (чёткий) размер на весь экран.
    if sys.platform == "win32":
        try:
            root.state('zoomed')
        except Exception:
            # Резервный вариант, если 'zoomed' недоступен
            root.update_idletasks()
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            root.geometry(f'{w}x{h}+0+0')
    else:
        # На macOS/Linux 'zoomed' не поддерживается - разворачиваем
        # через атрибут полноэкранного состояния окна
        try:
            root.attributes('-zoomed', True)
        except Exception:
            root.update_idletasks()
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            root.geometry(f'{w}x{h}+0+0')

    # Запускаем главный цикл приложения
    root.mainloop()