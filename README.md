# Youtube-Shorts-Uploader
Uploading videos with titles, tags, and descriptions via Google Drive and local AI any AI

ENG/RU


For the API (uploading to YouTube, downloading from Google Drive), we use console.cloud.google.com

In Google Cloud -> create new project -> we enable the Drive API and the YouTube API v3 -> seach OAuth consest screen -> Client -> Deskctop app -> Audience (External) -> Download Json

Download Unsloth https://unsloth.ai/ 

Download an AI model that can watch videos—in my example, it's Qwen3-VL-8B-Instruct

In unsloth, create an API key and paste it into the `local_api_key` line in `config.json`

