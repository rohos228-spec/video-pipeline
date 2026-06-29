# Быстрый тест ElevenLabs lab API (backend должен быть на :8765)

# 1) Подключиться через proxy IP
$body = @{
  api_key = "sk_..."          # или пусто — возьмёт из .env
  proxy_ip = "1.2.3.4"
  proxy_port = 8080
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/elevenlabs/connect" -Method POST -ContentType "application/json" -Body $body

# 2) Клон + замена слова во фрагменте (multipart — через curl удобнее)
# curl.exe -X POST http://127.0.0.1:8765/api/elevenlabs/clone-redub ^
#   -F voice_name=MyClone ^
#   -F sample=@sample.mp3 ^
#   -F source_audio=@voice_full.mp3 ^
#   -F start_s=12.5 -F end_s=15.8 ^
#   -F "fragment_text=тут было старое слово в тексте" ^
#   -F old_word=старое -F new_word=новое
