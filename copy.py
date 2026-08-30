from pathlib import Path
import shutil

source = Path(r"D:\Архив семьи")
destination = Path(r"E:\Архив семьи")

files = [file for file in source.rglob("*") if file.is_file()]
total = len(files)

copied = 0
skipped = 0
errors = 0

print(f"Всего файлов: {total}\n")

for current, file in enumerate(files, start=1):
    target = destination / file.relative_to(source)

    try:
        # Если файл уже существует и его размер совпадает — пропускаем
        if target.exists() and target.stat().st_size == file.stat().st_size:
            skipped += 1
            print(f"[{current}/{total}] ПРОПУСК: {file.relative_to(source)}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)

        copied += 1
        percent = current / total * 100

        print(
            f"[{current}/{total} ({percent:.1f}%)] "
            f"СКОПИРОВАН: {file.relative_to(source)}"
        )

    except OSError as e:
        errors += 1
        print(f"[{current}/{total}] ОШИБКА: {file.relative_to(source)}")
        print(f"    {e}")

print("\nКопирование завершено!")
print(f"Скопировано: {copied}")
print(f"Пропущено: {skipped}")
print(f"Ошибок: {errors}")
