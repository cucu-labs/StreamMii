<p align="right">
  <a href="https://t.me/streammiii">
    <img src="https://img.shields.io/badge/JOIN%20TELEGRAM-26A6E1?style=for-the-badge&logo=telegram&logoColor=white" alt="Join Telegram">
  </a>
</p>

# StreamMii

A simple tool that converts media into Wii compatible formats for smooth playback.

---

## 🚀 Features

* **GPU Acceleration Support:**
    * **AMD**
    * **NVIDIA** (NVENC)
    * **Intel** (QSV)
    * **CPU fallback** (not recommended due to lower quality/performance)
* **Automatic media metadata handling**

---

## 🛠️ Output Settings

| Parameter | Configuration |
| :--- | :--- |
| **Resolution** | 640x360 or 640x480 depending on aspect ratio |
| **Scaling** | Lanczos resampling |
| **Bitrate** | 1000 kbps |
| **FPS** | Usually matches source media |

---

## 📁 Supported Media Types

* Movies
* TV Shows
* <details>
  <summary>Adult Content </summary>

  Solo, Group, JAV

  </details>
* Audio
* Documentaries
* K-Drama
* Anime
* <details>
  <summary>Sports </summary>

  Team Sports > Football, Basketball, Volleyball, Rugby, Baseball, Softball, Handball  
  Combat Sports > Boxing, MMA, Wrestling, Karate  
  Winter Sports > Skiing, Snowboarding, Ice Skating, Bobsledding  
  Water Sports > Surfing, Rowing, Kayaking, Synchronized Swimming  
  Motor Sports > F1, MotoGP  
  Individual Sports > Athletics (Track and Field), Tennis, Golf, Pool, Swimming, Badminton, Table Tennis, Cycling, Gymnastics  
  Equestrian > Show Jumping, Dressage  
  Other > Cricket, Hockey

  </details>

> **More coming soon!**

---

## 💻 System Requirements

* `ffmpeg` must be installed and available in your system `PATH`.

### Installing packages

```bash
pip install guessit requests colorama
