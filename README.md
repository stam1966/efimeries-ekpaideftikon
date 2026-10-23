# Εφημερίες Εκπαιδευτικών

Εφαρμογή Windows (Python / Tkinter / SQLite) για δημιουργία και διαχείριση προγράμματος εφημεριών εκπαιδευτικών.

## Λήψη
Από τη σελίδα **[Releases](https://github.com/stam1966/efimeries-ekpaideftikon/releases/latest)**:
- `Efimeries_Setup_v…_x64.exe`: εγκατάσταση για 64-bit Windows (οι περισσότεροι υπολογιστές).
- `Efimeries_Setup_v…_x86.exe`: εγκατάσταση για 32-bit Windows.
- `EfimeriesPortable_v…_Portable.zip`: portable μορφή χωρίς εγκατάσταση (x64 + x86 + κοινός φάκελος `Shared`).

Η εγκατάσταση δεν χρειάζεται δικαιώματα διαχειριστή. Τα δεδομένα αποθηκεύονται στα **Έγγραφα → Εφημερίες Εκπαιδευτικών** (κοινά για x64/x86) και δεν διαγράφονται ποτέ από ενημέρωση ή απεγκατάσταση. Αν τα Windows δείξουν «Τα Windows προστάτευσαν τον υπολογιστή σας», πατήστε «Περισσότερες πληροφορίες» → «Εκτέλεση οπωσδήποτε».

## Δημιουργία έκδοσης (αυτόματα στο GitHub)
1. Άλλαξε το `APP_VERSION` στο `app.py` και κάνε commit.
2. Δημοσίευσε Release με νέο tag, π.χ. `v0.50` (ή `git tag v0.50 && git push --tags`).
3. Το workflow «Build Windows» φτιάχνει σε Windows: Setup.exe x64, Setup.exe x86 και το portable zip, δοκιμάζει εγκατάσταση/απεγκατάσταση και τα ανεβάζει στο Release (~10 λεπτά).
Χειροκίνητη εκτέλεση (χωρίς release): Actions → Run workflow.
Η εφαρμογή ελέγχει μόνη της για νέα έκδοση από τα Releases (χωρίς να στέλνει δεδομένα σχολείου).

## Προσωπικά δεδομένα
Οι φάκελοι `data/`, `backups/`, `exports/`, `logs/`, `Shared/` και τα `.db` / `.efbackup` εξαιρούνται (`.gitignore`). Μην τα ανεβάζεις.

Βλ. `CHANGELOG.md` και `docs/`. Αναφορά προβλήματος: [Issues](https://github.com/stam1966/efimeries-ekpaideftikon/issues).
