# AirSpeaker

macOSメニューバー常駐アプリ。コンピューターの音声を同一Wi-Fi上のGoogle Home Mini / Chromecastスピーカーにリアルタイムストリーミングします。

## 仕組み

```
macOS Multi-Output Device (Built-in Output + BlackHole)
    ↓
BlackHole 2ch (仮想オーディオデバイス)
    ↓
ffmpeg (PCMキャプチャ → MP3エンコード)
    ↓
HTTP Streaming Server (ローカル)
    ↓
Chromecast / Google Home (play_media)
```

## 前提条件

- macOS (Apple Silicon対応)
- Python 3.11+
- [BlackHole](https://existential.audio/blackhole/) (インストール済み)
- [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)
- Audio MIDI Setupで「Multi-Output Device」を作成済み
  - サブデバイス: Built-in Output + BlackHole 2ch

## セットアップ

```bash
# リポジトリをクローン
git clone https://github.com/Ainest/AirSpeaker.git
cd AirSpeaker

# venv作成 & 依存インストール
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使い方

```bash
source .venv/bin/activate
python -m airspeaker.main
```

1. メニューバーに 🔊 アイコンが表示される
2. 「デバイス一覧」からChromecast/Google Homeデバイスを選択
3. 「ストリーミング開始」をクリック
4. macOSの出力をMulti-Output Deviceに設定（システム設定 > サウンド）

## 注意事項

- Chromecastの仕様上、約2-3秒のレイテンシが発生します
- Mac とChromecastは同じWi-Fiサブネット上にある必要があります
- ストリーミング中はBlackHoleへの音声入力が必要です（Multi-Output Device経由）
