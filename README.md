# Εφημερίες Εκπαιδευτικών

Εφαρμογή Windows (Python / Tkinter / SQLite) για δημιουργία και διαχείριση προγράμματος εφημεριών εκπαιδευτικών.

## Λήψη
**Μόνιμοι σύνδεσμοι (δείχνουν πάντα στην τελευταία έκδοση, δεν αλλάζουν ποτέ):**
- [Setup 64-bit](https://github.com/stam1966/efimeries-ekpaideftikon/releases/latest/download/Efimeries_Setup_x64.exe) (οι περισσότεροι υπολογιστές)
- [Setup 32-bit](https://github.com/stam1966/efimeries-ekpaideftikon/releases/latest/download/Efimeries_Setup_x86.exe)
- [Portable zip](https://github.com/stam1966/efimeries-ekpaideftikon/releases/latest/download/EfimeriesPortable_Portable.zip)

Από τη σελίδα **[Releases](https://github.com/stam1966/efimeries-ekpaideftikon/releases/latest)**:
- `Efimeries_Setup_v…_x64.exe`: εγκατάσταση για 64-bit Windows (οι περισσότεροι υπολογιστές).
- `Efimeries_Setup_v…_x86.exe`: εγκατάσταση για 32-bit Windows.
- `EfimeriesPortable_v…_Portable.zip`: portable μορφή χωρίς εγκατάσταση (x64 + x86 + κοινός φάκελος `Shared`).

Η εγκατάσταση δεν χρειάζεται δικαιώματα διαχειριστή. Τα δεδομένα αποθηκεύονται στα **Έγγραφα → Εφημερίες Εκπαιδευτικών** (κοινά για x64/x86) και δεν διαγράφονται ποτέ από ενημέρωση ή απεγκατάσταση. Αν τα Windows δείξουν «Τα Windows προστάτευσαν τον υπολογιστή σας», πατήστε «Περισσότερες πληροφορίες» → «Εκτέλεση οπωσδήποτε».

## Δημιουργία έκδοσης (αυτόματα στο GitHub)
1. Άλλαξε το `APP_VERSION` στο `app.py` και πρόσθεσε τις αλλαγές στην κορυφή του `CHANGELOG.md`.
2. Ανέβασε (commit/push) στο `main`.
3. Το GitHub φτιάχνει μόνο του, σε Windows, το Setup.exe x64, το Setup.exe x86 και το portable zip, δοκιμάζει εγκατάσταση/απεγκατάσταση και δημοσιεύει νέο Release (~10 λεπτά). Δεν χρειάζεται tag ή κάτι άλλο.
Αν το `APP_VERSION` δεν αλλάξει, γίνεται μόνο έλεγχος build χωρίς νέο Release. Χειροκίνητη εκτέλεση: Actions → Run workflow.
Η εγκατεστημένη εφαρμογή ελέγχει μόνη της για νέα έκδοση και μπορεί να την κατεβάσει και να την εγκαταστήσει («Λήψη και εγκατάσταση τώρα»), χωρίς να στέλνει δεδομένα σχολείου.

## Προσωπικά δεδομένα
Οι φάκελοι `data/`, `backups/`, `exports/`, `logs/`, `Shared/` και τα `.db` / `.efbackup` εξαιρούνται (`.gitignore`). Μην τα ανεβάζεις.

Βλ. `CHANGELOG.md` και `docs/`. Αναφορά προβλήματος: [Issues](https://github.com/stam1966/efimeries-ekpaideftikon/issues).
