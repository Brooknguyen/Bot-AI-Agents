import os
import pygame

# Đường dẫn thư mục nhạc
MUSIC_FOLDER = r"E:\python LLM\music"  # <-- đổi thành thư mục của bạn

def list_mp3_files(folder_path):
    return [f for f in os.listdir(folder_path) if f.lower().endswith(".mp3")]

def find_mp3_by_name(folder_path, name):
    for f in list_mp3_files(folder_path):
        if name.lower() in f.lower():
            return os.path.join(folder_path, f)
    return None

def play_mp3(file_path):
    pygame.mixer.init()
    pygame.mixer.music.load(file_path)
    pygame.mixer.music.play()
    print(f"Đang phát: {file_path}")

def music_player():
    pygame.mixer.init()
    current_song = None

    while True:
        if not pygame.mixer.music.get_busy() and current_song is None:
            # Nhập tên bài mới nếu chưa có bài đang phát
            song_name = input("\nNhập tên bài hát (hoặc 'exit' để thoát): ").strip()
            if song_name.lower() == "exit":
                print("Thoát chương trình.")
                break

            song_path = find_mp3_by_name(MUSIC_FOLDER, song_name)
            if song_path:
                current_song = song_path
                pygame.mixer.music.load(current_song)
                pygame.mixer.music.play()
                print(f"Đang phát: {current_song}")
            else:
                print("Không tìm thấy bài hát trong thư mục.")
        else:
            # Nhập lệnh khi bài đang phát
            cmd = input("Nhập lệnh (pause/resume/stop/next/exit): ").strip().lower()
            if cmd == "pause":
                pygame.mixer.music.pause()
                print("Đã tạm dừng.")
            elif cmd == "resume":
                pygame.mixer.music.unpause()
                print("Đã tiếp tục phát.")
            elif cmd == "stop":
                pygame.mixer.music.stop()
                print("Đã dừng phát.")
                current_song = None
            elif cmd == "next":
                pygame.mixer.music.stop()
                current_song = None
            elif cmd == "exit":
                pygame.mixer.music.stop()
                print("Thoát chương trình.")
                break
            else:
                print("Lệnh không hợp lệ. Dùng: pause/resume/stop/next/exit")

if __name__ == "__main__":
    music_player()
