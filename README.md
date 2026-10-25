# StreamMii 0.0.5

A simple tool that converts media into Wii-compatible formats for smooth playback.

**Recommended Python version:** 3.14

---

## What's New

* Colorama is now used for purple text.
* Safer organizer, preview, resume, and verified conversions.

## Features

* **GPU Acceleration Support:**
  * **AMD**
  * **NVIDIA** (NVENC)
  * **Intel** (QSV)
  * **CPU fallback** (not recommended due to lower quality/performance)
* **Automatic media metadata handling** (NEW ORGANIZER!)

## Output Settings

| Parameter | Configuration |
| :--- | :--- |
| **Resolution** | 640×360 wide; 640×480 otherwise |
| **Scaling** | Lanczos resampling |
| **Bitrate** | 1000 kbps |
| **FPS** | Up to 30 FPS |
| **Audio** | AAC stereo, 128 kbps |

## Supported Media Types

* Movies
* TV Shows
* <details>
  <summary>Adult Content</summary>

  Solo, Group, JAV, LGBT (solo, group)

  </details>
* Audio
* Documentaries
* K-Drama
* Anime
* <details>
  <summary>Sports</summary>

  * **Team Sports:** Football, Basketball, Volleyball, Rugby, Baseball, Softball, Handball
  * **Combat Sports:** Boxing, MMA, Wrestling, Karate
  * **Winter Sports:** Skiing, Snowboarding, Ice Skating, Bobsledding
  * **Water Sports:** Surfing, Rowing, Kayaking, Synchronized Swimming
  * **Motor Sports:** F1, MotoGP
  * **Individual Sports:** Athletics (Track and Field), Tennis, Golf, Pool, Swimming, Badminton, Table Tennis, Cycling, Gymnastics
  * **Equestrian:** Show Jumping, Dressage
  * **Other:** Cricket, Hockey

  </details>

> **More coming soon!**

## System Requirements

* `ffmpeg-full` must be installed and available in your system `PATH`.

### Installing Packages

```bash
pip install -r requirements.txt
```