# Code signing policy / Πολιτική ψηφιακής υπογραφής

**Εφημερίες Εκπαιδευτικών** — Ιδέα και σχεδιασμός: Σταμάτης Κοκολόγος, εκπαιδευτικός ΠΕ70, 22ο Δημοτικό Σχολείο Λαμίας.

## English

**Current status: the Windows releases are not code-signed.** An application to the SignPath Foundation free program (November 2026) was not accepted because the project is still new and not yet widely known; it may be resubmitted later. Until then Windows SmartScreen may show "Windows protected your PC": choose "More info" → "Run anyway".

**How releases are built:** the Windows installers published on [GitHub Releases](https://github.com/stam1966/efimeries-ekpaideftikon/releases) are built from this repository by GitHub Actions (`.github/workflows/build.yml`); no binaries are built on personal computers.

**Team roles**
| Role | Members |
|---|---|
| Committers and reviewers | [stam1966](https://github.com/stam1966) (Stamatis Kokologos, owner) |
| Approvers | [stam1966](https://github.com/stam1966) (Stamatis Kokologos, owner) |

The maintainer uses multi-factor authentication for GitHub. If code signing is introduced, every release will require manual approval before signing. Changes from non-members (pull requests) are reviewed before merging.

**Privacy policy:** This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. Exception: checking for new versions on GitHub (can be disabled in the settings; no user data is sent).

**Uninstallation:** the installers register a normal Windows uninstaller (Settings → Apps). User data is kept in the data folder and can be deleted manually.

**License:** GNU General Public License v3.0 ([LICENSE](LICENSE)). Third-party components: CPython (PSF License), Tcl/Tk, sv-ttk theme (MIT), PyInstaller bootloader (GPL with exception). Their licenses are included in the distribution.

## Ελληνικά

**Τρέχουσα κατάσταση: οι εκδόσεις για Windows δεν έχουν ψηφιακή υπογραφή.** Η αίτηση στο δωρεάν πρόγραμμα του SignPath Foundation (Νοέμβριος 2026) δεν έγινε δεκτή, επειδή το έργο είναι ακόμη νέο και όχι ευρέως γνωστό· μπορεί να υποβληθεί ξανά αργότερα. Μέχρι τότε τα Windows μπορεί να δείξουν «Τα Windows προστάτευσαν τον υπολογιστή σας»: πατήστε «Περισσότερες πληροφορίες» → «Εκτέλεση οπωσδήποτε». Τα αρχεία εγκατάστασης χτίζονται αυτόματα από τον δημόσιο κώδικα αυτού του αποθετηρίου στο GitHub Actions, όχι σε προσωπικό υπολογιστή.

**Ιδιωτικότητα:** η εφαρμογή δεν στέλνει πληροφορίες σε άλλα συστήματα, εκτός αν το ζητήσει ο χρήστης. Τα δεδομένα του σχολείου μένουν στον υπολογιστή ή στον κοινό φάκελο του σχολείου.
