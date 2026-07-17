### splitter

`splitter.py` — режет аудиодорожку на  кусочки короче 15с, алгорит можно дорабатывать под свой вкус. В основном задействован FFMPEG.

### transcribe

`transcribe.py` — создаёт в каталоге с семплами файл `metadata.csv` c распознанным текстом. Использует `onnx-asr`.

### sampleselector 
`sampleselectorx.pyw` и `fast_cutter_x.py` — инструмент для отбора аудиосемплов (после сплиттера) / комплексной правки датасета (после этапа транскрибации). Требуемые зависимости указаны в начале кода.

<img height="400" alt="image" src="https://github.com/user-attachments/assets/975d7194-80fb-44a9-92d9-eab0dac19ae7" />

### Клиенты для любых wyoming tts серверов

Полезны при обучении для сравнения разных версий моделей.

- `wp_tts_client.py` — вариант на python, требует установки библиотек
- `Wyoming_client[go].exe` — портативный клиент для win
- `WyomingClietn.apk` — вариант для андроид


| | | |
| :---: | :---: | :---: |
| <img height="300" alt="image" src="https://github.com/user-attachments/assets/859bdffc-e92d-424b-b179-c0f9be4c4d76" /> | <img height="300" alt="image" src="https://github.com/user-attachments/assets/f609e2ca-4e1e-4ca8-8f2c-6ee123d52f5d" /> | <img height="300" alt="image" src="https://github.com/user-attachments/assets/09c39a6a-35ac-4a3d-ba73-59abc1fad92d" /> |
