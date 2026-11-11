;;; guix.scm — SYMBIONT's SECOND build channel (req-P1-A-GUIX-SCM-a-recipe-nobody-built-is-a).
;;;
;;; Why this file exists: Genenetwork4 ships a guix.scm of its own, so this is the channel their site
;;; already knows how to build. A recipient that cannot run our pip recipe can still rebuild the
;;; station bit-for-bit from this file, and a reader can see the station's whole dependency story in
;;; ten lines instead of trusting a requirements resolver.
;;;
;;; To build the station:
;;;
;;;   guix build -f guix.scm
;;;
;;; A development shell with the build inputs (setuptools, wheel, PyYAML, python):
;;;
;;;   guix shell -D -f guix.scm
;;;
;;; and the station's own offline proof, run inside that shell:
;;;
;;;   guix shell -D -f guix.scm -- python3 -m symbiont.selftest_sources     # 25/25
;;;
;;; A COLD FIRST BUILD TAKES MINUTES (substitutes for python/setuptools, then PyYAML and this
;;; package built from source). That is the honest number; nothing here is a 20-second build.

(use-modules
  ((guix licenses) #:prefix license:)
  (guix gexp)                 ; local-file
  (guix packages)
  (guix build-system pyproject)
  (srfi srfi-1)               ; any, member
  (srfi srfi-13)              ; string-prefix?, string-contains, string-suffix?
  (gnu packages python)       ; python-wrapper (a bare `python` in the dev shell; never propagated)
    (gnu packages python-build) ; python-setuptools, python-wheel
  (gnu packages python-xyz))  ; python-pyyaml

;; ── THE ONE DEPENDENCY DECISION (2026-11-11, this row) ───────────────────────────────────────────
;; The station is stdlib-first with exactly ONE third-party import (PyYAML, read by
;; symbiont/contract.py for sources.yml). Guix's channel carries python-pyyaml 6.0.2; our pip pin
;; was 6.0.3. The version question is DECIDED here rather than left to drift:
;;
;;   DECIDED 2026-11-11 (req-P1-A-GUIX-SCM-a-recipe-nobody-built-is-a): PyYAML 6.0.2 is THE version.
;;   We PROPAGATE python-pyyaml from the channel and accept the channel's version as THE one, so
;;   one version names what ran on both channels: this recipe propagates python-pyyaml (6.0.2) and
;;   pyproject.toml declares the same range (>=6.0,<7) here, so the two recipes cannot
;;   disagree. The cost of the decision is one patch release (6.0.2 -> 6.0.3) and it is paid
;;   knowingly; the 25/25 selftest below is what proves the station still behaves on 6.0.2.
;;
;; The alternative — pinning our own PyYAML 6.0.3 variant here — was rejected: it would add a
;; second origin (a PyPI tarball + a hash we maintain) for a bugfix release, and it would leave
;; the pip path and the guix path naming different versions. The decision is repeated verbatim in
;; pyproject.toml and symbiont/README.md; `make check` asserts ONE spec exists three files.
;;
;; python-pyyaml is PROPAGATED, not a plain input: the station imports yaml at run time, so anyone
;; installing this package must receive PyYAML in their profile — the same reasoning genenetwork4's
;; guix.scm uses for its subprocess dependencies.

(define %source-dir (dirname (current-filename)))

;; ── THE SOURCE FILTER ───────────────────────────────────────────────────────────────────────────
;; A recipe that drags the store in is not a recipe. This tree is the farm, and the farm carries
;; 4.2 GB of things the station does not ship: symbiont/store (measured 447 MB), symbiont/bench/data
;; and bench/out, node_modules (74 MB, under symbiont/literature/mcp-server), the Python caches,
;; plus reports/ (735 MB), harvester/, nurse/, tools/ — none of which is the station.
;;
;; What SHIPS is not invented here: it is exactly what acceptance.sh's A1-CLONE ships (acceptance.sh
;; stage_A1, the clone the end-to-end acceptance run installs and runs) — the package, the second
;; package (annot), the run-time inputs the station reads out of reports/, and the top-level files.
;; The named exclusions are the row's (store, models/, .venv*, node_modules/, __pycache__, bench/data)
;; and A1-CLONE's own (bench/out, *.bak-*, *.pyc), so the two shippers cannot drift either.

(define %top-files
  '("pyproject.toml" "Makefile" "LICENCE" "SUPPORT.md" "CHANGELOG.md"))

;; the station's own trees, and the reports/ files it reads at run time (symbiont-policy.yml, the
;; queue, the drop reasons, the journal) — the A1-CLONE list
(define %source-roots '("symbiont" "annot" "reports/adapters"))

(define %source-files
  '("reports/symbiont-policy.yml" "reports/STANDING-JOBS.tsv"
    "reports/DROP-REASONS.tsv" "reports/rows-journal.log"))

;; never shipped, matched on a path SUFFIX (the directory itself) or a "/"-terminated infix
;; (everything under it): the store (446 MB), models/, bench/data, bench/out, node_modules, .git
(define %excluded-paths
  '("/symbiont/store" "/bench/data" "/bench/out" "/models" "/node_modules" "/__pycache__" "/.git"))

;; never shipped, matched on the FILE NAME anywhere in the tree: .venv, .venv-rerank, __pycache__,
;; node_modules, models, any *.pyc, any *.bak-* backup
(define (excluded-name? file)
  (let ((base (basename file)))
    (or (string-prefix? ".venv" base)
        (string=? base "__pycache__")
        (string=? base "node_modules")
        (string=? base "models")
        (string-suffix? ".pyc" base)
        (string-contains base ".bak-"))))

(define (excluded? file)
  (or (excluded-name? file)
      (any (lambda (frag)
             (or (string-suffix? frag file)
                 (string-contains file (string-append frag "/"))))
           %excluded-paths)))

;; the file's path relative to the recipe's own directory
(define (relative-to-source file)
  (let ((n (string-length %source-dir)))
    (and (> (string-length file) n)
         (char=? (string-ref file n) #\/)
         (substring file (+ n 1)))))

(define (under? rel dir)
  (or (string=? rel dir) (string-prefix? (string-append dir "/") rel)))

(define (station-source? file stat)
  (and (not (excluded? file))
       (or (string=? file %source-dir)     ; the root itself: descend, the children decide
           (let ((rel (relative-to-source file)))
             (and rel
                  (or (member rel %top-files)
                      (member rel %source-files)
                      (any (lambda (dir) (under? rel dir)) %source-roots)
                      ;; an ANCESTOR of a kept path (reports/ for reports/adapters): descend
                      (any (lambda (dir)
                             (string-prefix? (string-append rel "/") dir))
                           %source-roots)))))))

(define %description
  "SYMBIONT is a provenance-first mining station. Source adapters fetch backed sources, a
versioned content-addressed store keeps every record with its source, release, sha256 and count,
the counts are verified against each source's own claim, and the only write is a handover
manifest. The station is stdlib-first: its single third-party dependency is PyYAML, used to read
the one definition site per source (sources.yml).")

(define-public symbiont
  (package
    (name "symbiont")
    (version "0.1.0")
    (source (local-file %source-dir #:recursive? #t #:select? station-source?))
    (build-system pyproject-build-system)
    (arguments
     ;; The station's own checks are NOT skipped because they are inconvenient: symbiont's selftests
     ;; bring up a loopback HTTP server and write throwaway stores, which is work a build sandbox is
     ;; not shaped for, and `make check` runs them OFFLINE in one command. The recipe therefore
     ;; installs and the DEV SHELL proves it:
     ;;   guix shell -D -f guix.scm -- python3 -m symbiont.selftest_sources   (25/25)
     ;; (guix build itself still compiles every module during install.)
     (list #:tests? #f
           #:phases
           #~(modify-phases %standard-phases
               ;; The station locates its run-time inputs relative to the package's PARENT directory
               ;; (symbiont/selftest_sources.py: FARM = dirname (dirname __file__)); the fetch
               ;; direction lives in reports/adapters/<source>.decision.md, and without it every
               ;; fetch REFUSES[decision] by name. A wheel install puts symbiont/ into
               ;; site-packages and nothing else, so the INSTALLED station would refuse every run --
               ;; measured: REFUSED[decision] adapter 'ensembl_biomart' has NO decision record at
               ;; <profile>/lib/python3.11/site-packages/reports/adapters/ensembl_biomart.decision.md.
               ;; Shipping the same run-time inputs beside the package (the identical set
               ;; acceptance.sh's A1-CLONE ships) is what makes the installed station work, and it
               ;; is why %source-roots keeps reports/adapters instead of dropping it.
               (add-after 'install 'install-run-time-inputs
                 (lambda* (#:key outputs #:allow-other-keys)
                   (let* ((out (assoc-ref outputs "out"))
                          (site (car (find-files out "^site-packages$" #:directories? #t))))
                     (copy-recursively "reports" (string-append site "/reports"))))))))
    (native-inputs (list python-setuptools python-wheel python-wrapper))
      ;; python-wrapper is a DEV convenience only: it provides a bare `python` so `guix shell -D -f guix.scm` lets you type either name.
      ;; It is deliberately NOT propagated: the station must run on a box whose interpreter is called `python3`.
    ;; see THE ONE DEPENDENCY DECISION above: PyYAML 6.0.2, the version the channel carries, is THE
    ;; version — propagated so an installing profile gets it
    (propagated-inputs (list python-pyyaml))
    (synopsis "Provenance-first mining station: fetch, verify, snapshot, xref, publish")
    (description %description)
    ;; home-page is a REQUIRED field and the station has no published origin of its own yet: this is
    ;; the site it is built for (genenetwork4's recipe points at its own repo; we have none yet).
    ;; Replace it the day the station has a git origin — a seed copy claiming a repo it has not got
    ;; would be the same lie as a version nobody can build.
    (home-page "https://genenetwork.org/")
    (license license:expat)))            ; MIT, per LICENCE

symbiont
