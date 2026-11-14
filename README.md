# MediaConverter GUI

Lokalny panel do konwersji i przetwarzania plików multimedialnych. Interfejs jest zbudowany w NiceGUI, a operacje wykonują FFmpeg i FFprobe. Historia, ustawienia i własne profile są przechowywane w SQLite. Aplikacja nie wysyła multimediów do chmury i nie wymaga połączenia z Internetem po zainstalowaniu zależności.

## Wymagania

- Windows 10 lub 11, Python **3.12 lub nowszy** i przeglądarka internetowa.
- FFmpeg **oraz** FFprobe. Oba programy powinny pochodzić z tej samej dystrybucji FFmpeg i być dostępne w `PATH` albo wskazane w ustawieniach aplikacji. Oficjalna strona projektu: https://ffmpeg.org/download.html.
- Folder projektu, folder wynikowy i folder tymczasowy z wystarczającą ilością wolnego miejsca. Projekt powinien znajdować się w katalogu z prawem zapisu.
- Połączenie z Internetem jest potrzebne do pobrania Pythona, FFmpeg i bibliotek podczas instalacji, chyba że masz je już lokalnie lub korzystasz z lokalnego repozytorium pakietów.

Obsługiwane kodeki i akceleratory zależą od kompilacji FFmpeg, sterowników i sprzętu. Wybór kodeka, którego dany FFmpeg nie zawiera, daje czytelny błąd przed dodaniem zadania.

## Instalacja i uruchomienie

1. Zainstaluj Python 3.12+ oraz FFmpeg z FFprobe. Jeśli nie dodasz FFmpeg do `PATH`, wpisz pełne ścieżki do obu plików EXE w **Ustawieniach** po uruchomieniu panelu.
2. Uruchom `install.bat`. Skrypt przygotuje `.venv` i zainstaluje dokładne wersje z `requirements.lock`. Rozpoznaje również istniejący interpreter Python 3.12 zarządzany przez `uv`.
3. Uruchom `start.bat`. Aplikacja sprawdzi środowisko, wybierze wolny port, uruchomi serwer na `127.0.0.1` i otworzy przeglądarkę. Jeśli FFmpeg nie zostanie znaleziony, panel nadal umożliwi ustawienie jego ścieżek.
4. Pozostaw okno skryptu otwarte podczas pracy. Zamknięcie procesu kończy serwer i przerywa aktywne zadania.

Ręczne uruchomienie po instalacji:

```bat
.venv\Scripts\python.exe main.py
```

Parametry `--port 8765` oraz `--no-browser` pozwalają odpowiednio wybrać port i wyłączyć automatyczne otwieranie przeglądarki. Adres jest wyświetlany w konsoli. Możliwa jest tylko jedna instancja aplikacji dla jednego folderu danych.

`update.bat` ponownie instaluje wersje zapisane w `requirements.lock`; nie pobiera nowej wersji kodu projektu ani nie aktualizuje FFmpeg. Aby zmienić wersje bibliotek, najpierw świadomie zaktualizuj i przetestuj pliki zależności, a następnie uruchom ten skrypt. `requirements.txt` zawiera główne zależności, `requirements.lock` pełny zestaw wersji użyty w testach, również z narzędziami testowymi.

## Pierwsza konwersja

1. Otwórz **Konwerter wideo**, **Konwerter audio** lub **Wideo na audio**.
2. Przeciągnij pliki do obszaru dodawania albo użyj przycisku `+`. Alternatywnie rozwiń **Dodaj pliki według ścieżek lokalnych** i podaj pełną ścieżkę w każdym wierszu lub pozostaw pole puste i kliknij **Dodaj ścieżki**, aby wybrać pliki w oknie systemowym. Ta metoda korzysta z oryginalnych plików bez tworzenia kopii. Wysyłanie w przeglądarce odbywa się wyłącznie do lokalnego procesu i tworzy lokalną kopię roboczą.
3. Kliknij ikonę informacji przy pliku, aby obejrzeć podgląd, parametry i indeksy strumieni. Odtwarzanie zależy od kodeków przeglądarki; brak podglądu nie oznacza, że FFmpeg nie może przekonwertować pliku.
4. Wybierz format, ewentualnie profil, i wpisz istniejący folder wynikowy. Przeglądarka nie udostępnia aplikacji pełnych ścieżek lokalnych z okna wyboru plików, dlatego folder podaje się jako ścieżkę na komputerze.
5. Opcjonalnie rozwiń ustawienia zaawansowane. **Podgląd komendy** pokazuje polecenie dla pierwszego zadania, a jego ostateczna postać znajduje się w szczegółach kolejki.
6. Kliknij **Konwertuj**. Postęp, czas, szacowany pozostały czas i prędkość są odczytywane z rzeczywistego wyjścia FFmpeg. Gdy czas trwania źródła jest nieznany, wiarygodny procent lub ETA mogą być niedostępne.

Przy wielu plikach powstaje osobne zadanie dla każdego z nich. Widok **Konwersja wsadowa** udostępnia wspólne ustawienia; limit równoległych zadań zmienisz w **Ustawieniach**. W kolejce możesz anulować zadanie, ponowić błędne lub przerwane, zobaczyć log, pobrać wynik i otworzyć jego folder. Czyszczenie kolejki usuwa wpisy zakończonych zadań z historii, ale nie usuwa wyników.

## Formaty, profile i narzędzia

- Wideo: MP4, MKV, AVI, MOV, WEBM, FLV, WMV, MPEG, TS i M4V; dostępne kodeki zależą od wybranego kontenera. M4V jest zapisywany jako kontener MP4 z rozszerzeniem `.m4v`.
- Audio: MP3, WAV, FLAC, AAC, M4A, OGG/Vorbis, OPUS, WMA, AIFF i ALAC w kontenerze M4A. Można również wyodrębnić audio z wideo.
- Gotowe profile wideo i audio oraz możliwość zapisania lub usunięcia własnych profili. Profil kopiowania zachowuje strumienie bez rekompresji tylko wtedy, gdy kodeki pasują do kontenera. FLAC, WAV, AIFF i ALAC mogą zapisać dźwięk bezstratnie, ale nie przywracają jakości utraconej wcześniej.
- W ustawieniach zaawansowanych dostępne są m.in. kodek, CRF/CQ lub bitrate, preset CPU, format pikseli, liczba wątków, FPS, rozdzielczość, proporcje, obrót, szybkość, zakres czasu w sekundach, bitrate audio, próbkowanie, kanały, normalizacja, głośność i metadane. Wymiary są parzyste, a skalowanie zachowuje proporcje źródła w zadanym prostokącie. Normalizacja używa filtra `loudnorm` w przebiegu dynamicznym.
- Indeksy ścieżek audio i napisów pochodzą z FFprobe i są widoczne w szczegółach pliku. Domyślnie wybierana jest pierwsza ścieżka audio, a napisy nie są dołączane. Można wskazać ścieżkę napisów lub pełną ścieżkę do dodatkowego pliku napisów oraz zastąpić audio innym plikiem. Dołączanie napisów jest dostępne dla MP4, M4V, MOV i MKV.
- **Narzędzia multimedialne** obejmują wideo → GIF, łączenie wideo lub audio, ekstrakcję klatek do archiwum ZIP i tworzenie miniatury JPG. GIF → wideo wykonuje się w konwerterze wideo. Przycinanie, obrót, usuwanie audio i zmiana prędkości są dostępne w ustawieniach konwersji.
- Łączenie wykorzystuje demukser concat i wymaga zgodnego układu strumieni, kodeków, parametrów obrazu i dźwięku oraz podstaw czasu. Pliki są łączone w kolejności dodania. Dane niezgodne są odrzucane z komunikatem; przed łączeniem przekonwertuj je do jednakowych parametrów.

Nie wszystkie kombinacje formatu, kodeka, próbkowania, formatu pikseli i filtrów są poprawne. Kontener ogranicza listy kodeków, dostępność enkodera i wybrane parametry są sprawdzane przed uruchomieniem, a dodatkowe ograniczenia źródła lub sprzętu mogą zostać zgłoszone w logu FFmpeg. Kopiowanie strumienia nie pozwala stosować filtrów zmieniających ten strumień.

## GPU i jakość

Podczas startu aplikacja sprawdza dostępne enkodery i wykonuje jednoklatkowy test NVIDIA NVENC, Intel QSV i AMD AMF dla dostępnych kodeków H.264, HEVC i AV1. Wybór `Automatycznie` korzysta z pierwszego działającego akceleratora, a `CPU` wymusza kodowanie programowe. Opcjonalne dekodowanie CUDA dotyczy NVIDIA; obsługa może zależeć od pliku i sterownika. W razie błędu sprzętu aplikacja może ponowić zadanie na CPU, jeśli włączono taką opcję. Komunikaty o tym są widoczne w logu zadania. W trakcie początkowego testu automatyczny wybór może skorzystać z CPU.

Takie same wartości bitrate lub jakości nie oznaczają identycznego rezultatu dla enkoderów sprzętowych i CPU. Obciążenie GPU na dashboardzie jest pokazywane, jeśli dostępne jest narzędzie `nvidia-smi`; dla pozostałych konfiguracji może widnieć „Niedostępne”.

## Dane, bezpieczeństwo i pliki tymczasowe

- Serwer domyślnie słucha tylko na `127.0.0.1`. Odrzuca obce nagłówki `Host` i `Origin`, także przy połączeniu WebSocket. FFmpeg i FFprobe otrzymują listę lokalnych protokołów, co blokuje pobieranie z sieci np. przez zdalny adres w playliście. Procesy są uruchamiane bez `shell=True`.
- Panel jest przeznaczony dla zaufanego użytkownika lokalnego i nie ma własnego logowania. Inne procesy działające na tym komputerze mogą mieć dostęp do lokalnego serwera i danych. Nie wystawiaj go na Internet.
- Oryginalne pliki źródłowe nie są usuwane ani nadpisywane. Wynik ma nową nazwę; w razie kolizji dostaje kolejny numer. Najpierw jest tworzony plik częściowy, a gotowy wynik jest publikowany bez nadpisania istniejącego pliku.
- Po anulowaniu lub błędzie plik częściowy jest usuwany, chyba że zaznaczono jego zachowanie. Przy nieoczekiwanym zakończeniu procesu pliki częściowe bez tej opcji są sprzątane przy następnym uruchomieniu. Zadania oczekujące lub przetwarzane są oznaczane jako przerwane i można je ponowić.
- SQLite i lokalne kopie plików dodanych przez przeglądarkę znajdują się w `data/`. Wewnętrzny katalog `data/temp` jest czyszczony po restarcie. Kopie z `data/uploads` starsze niż 7 dni są usuwane podczas uruchomienia, o ile nie są potrzebne do ponowienia błędnych, anulowanych lub przerwanych zadań. Kopia przeglądarkowa nie jest oryginalnym plikiem użytkownika. Przy przetwarzaniu dużych plików najoszczędniejsze jest dodawanie pełnych ścieżek lokalnych.
- Kopiowanie i odczyt plików odbywają się blokami lub przez FFmpeg; całe multimedia nie są ładowane do pamięci Pythona. Ścieżki sieciowe UNC i ścieżki zawierające `..` są odrzucane.

## Testy

Po instalacji:

```bat
.venv\Scripts\python.exe -m pytest -q
```

Testy tworzą krótkie lokalne pliki przez FFmpeg. Sprawdzają metadane i polskie ścieżki, polecenia, rzeczywiste konwersje i łączenie, ekstrakcję audio i klatek, kolejkę, anulowanie, odzyskiwanie stanu, fallback CPU, ochronę przed dostępem sieciowym oraz serwer i zdarzenia interfejsu, w tym dodawanie i konwersję plików. Wymagają działających `ffmpeg` i `ffprobe` w `PATH`. Brak fizycznych kart poszczególnych producentów oznacza, że ich rzeczywiste kodowanie trzeba dodatkowo zweryfikować na danym sprzęcie.

## Układ projektu

`main.py` uruchamia serwer; `app/models.py`, `app/engine.py`, `app/media.py`, `app/jobs.py`, `app/files.py` i `app/store.py` odpowiadają za walidację, FFmpeg, kolejkę oraz zapis danych. `app/ui.py` i `assets/app.css` tworzą panel, a `tests/` zawiera testy. Nie jest potrzebny zewnętrzny serwer WWW ani usługa chmurowa.
