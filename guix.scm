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
  (gnu packages python-build) ; python-setuptools, python-wheel
  (guix download)             ; url-fetch — the one-version origin below
  (guix build-system python)  ; pypi-uri
  (gnu packages python-xyz))  ; python-pyyaml (the channel's recipe, which we inherit)

;; ── THE ONE DEPENDENCY DECISION (2026-11-11, this row) ───────────────────────────────────────────
;; The station is stdlib-first with exactly ONE third-party import (PyYAML, read by
;; symbiont/contract.py for sources.yml). The version question is DECIDED here rather than left to
;; drift:
;;
;;   DECIDED 2026-11-11 (req-P1-ONE-DEPENDENCY-ONE-VERSION-AND-A-CHECK): PyYAML 6.0.3 is THE
;;   version — the version the suites actually ran on, so it is the one every file must name.
;;   THIS recipe builds that 6.0.3: %python-pyyaml-6.0.3 below inherits the channel's
;;   python-pyyaml and overrides version + source, and it is PROPAGATED, so the profile the
;;   station is installed into receives exactly the version pyproject.toml pins. One token, two
;;   channels.
;;
;;   REVERSED by this row: the earlier decision propagated the channel's package and moved the pip
;;   pin down to match it. That made the shipped version a version nobody's suite had run on, and
;;   it left the tree's prose arguing about which number was real. The cost of the reversal is the
;;   one origin above (a PyPI sdist and the hash below); the proof is the dev-shell selftest.
;;
;; THE DECISION IS NOT A COMMENT STANDING WHERE AN ASSERTION SHOULD BE. This file used to state its
;; own acceptance test as an instruction to the reader — "grep PyYAML across those three files" —
;; and when it was finally run it returned ZERO hits. The instruction is now an assertion, and it
;; lives in `make check` (target `dependency-version`): it greps every file in the tree, fails on a
;; second version (naming every file that states one), fails on a range, and fails on any file that
;; names the dependency with no version at all.
;;
;; python-pyyaml is PROPAGATED, not a plain input: the station imports yaml at run time, so anyone
;; installing this package must receive PyYAML in their profile — the same reasoning genenetwork4's
;; guix.scm uses for its subprocess dependencies.

;; ── THE ONE VERSION, BUILT ──────────────────────────────────────────────────────────────────────
;; The channel's python-pyyaml is one patch release behind what the station ships, and pyproject.toml
;; pins PyYAML==6.0.3 — so propagating the channel package would hand the station a version its own
;; metadata forbids (measured: a pin the profile cannot satisfy stops the build, it is not a
;; warning). This definition pins 6.0.3 over the channel's recipe: same build system, same inputs,
;; one variable changed. sha256 of PyYAML-6.0.3.tar.gz on PyPI: (base32
;; "03qrhk1vz2g12xgy9mdr4p3ibvxprch710gq9kxj5pr16hvj6rnp").
(define python-pyyaml-6.0.3
  (package
    (inherit python-pyyaml)
    (version "6.0.3")
    (source (origin
              (method url-fetch)
              (uri (pypi-uri "PyYAML" version))
              (sha256
               (base32
                "03qrhk1vz2g12xgy9mdr4p3ibvxprch710gq9kxj5pr16hvj6rnp"))))))

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
    (native-inputs (list python-setuptools python-wheel))
    ;; see THE ONE DEPENDENCY DECISION above: the ONE version is 6.0.3, built by the variant above
    ;; and propagated so an installing profile receives exactly what pyproject.toml pins
    (propagated-inputs (list python-pyyaml-6.0.3))
    (synopsis "Provenance-first mining station: fetch, verify, snapshot, xref, publish")
    (description %description)
    ;; home-page is a REQUIRED field and the station has no published origin of its own yet: this is
    ;; the site it is built for (genenetwork4's recipe points at its own repo; we have none yet).
    ;; Replace it the day the station has a git origin — a seed copy claiming a repo it has not got
    ;; would be the same lie as a version nobody can build.
    (home-page "https://genenetwork.org/")
    (license license:expat)))            ; MIT, per LICENCE

symbiont
