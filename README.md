# Εφημερίες Εκπαιδευτικών Portable

Εφαρμογή Windows (Python / Tkinter / SQLite) για δημιουργία και διαχείριση προγράμματος εφημεριών εκπαιδευτικών.
Δεν χρειάζεται εγκατάσταση πακέτων για να τρέξει από τον κώδικα: `python app.py`.

## Δημιουργία αρχείων διανομής (x64 + x86)
Γίνεται αυτόματα στο GitHub, χωρίς Windows στον υπολογιστή σου:
1. Άλλαξε το `APP_VERSION` στο `app.py` και κάνε commit.
2. Φτιάξε tag: `git tag v0.49 && git push --tags`.
3. Στο GitHub → **Actions** τρέχει το «Build Windows (x64 + x86)» (~5–10 λεπτά).
4. Το έτοιμο `EfimeriesPortable_v0_49_Distribution.zip` εμφανίζεται στα **Releases** (και ως artifact στο run).

Το zip περιέχει `x64/`, `x86/`, τον κοινό φάκελο `Shared/` (data, backups, exports), τον οδηγό PDF και το README.
Το όνομα του zip παίρνει την έκδοση από το `APP_VERSION`. Δεν υπάρχει σταθερός αριθμός πουθενά.
Χειροκίνητη εκτέλεση: Actions → Run workflow.

## Προσωπικά δεδομένα
Οι φάκελοι `data/`, `backups/`, `exports/`, `logs/`, `Shared/` και τα αρχεία `.db` / `.efbackup` εξαιρούνται από το αποθετήριο (`.gitignore`). Μην τα ανεβάζεις.

## Τοπικά builds
Τα `build_*.bat` παραμένουν για χειροκίνητη δημιουργία στα Windows. Βλ. `CHANGELOG.md` και `docs/`.
