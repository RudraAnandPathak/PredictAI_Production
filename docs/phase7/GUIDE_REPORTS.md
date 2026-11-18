# Reports guide (including Research-to-Report)

**Create.** Reports → New report → choose a project and what it is built from: a blank outline, research results, registered model versions, saved analyses/scenarios (the API also accepts dataset analysis runs and exam analyses). A report is **assembled from results you already have**. Creating, editing, exporting or scheduling it never re-runs research, training or calculations.

**Ten templates:** research report, academic literature review, data analysis, predictive modelling, Exam Intelligence, scientific research, business intelligence, operational, model evaluation, scenario analysis. Your own templates can be saved (`/reports/templates`).

**Evidence.** Every statement is a *claim* with a kind (fact, calculation, prediction, interpretation, suggestion, process, note) and links to evidence entries (E1…) kept in a report evidence register: source, location, verification status, calculation, dataset/model version, limitations and uncertainty. Statements with no evidence are flagged. Citation styles: APA, MLA, Harvard, IEEE, Chicago; missing details are shown as missing rather than invented.

**Readiness.** *Ready*, *Needs review* (statements to check, unverified sources, or sections resting on research that has since changed or been removed) or *Incomplete* (sections with no supporting evidence). Exports of incomplete reports say so on their first page.

**Edit.** Edit title, headings, statements and citation style; add/remove sections, tables, notes. Each save creates a **new version**; old versions are never changed and can be viewed or compared. Edits carry the version they were based on, so two people cannot silently overwrite each other (conflict error).

**Export.** PDF, Word, PowerPoint, Markdown, CSV (tables), Excel (tables), PNG (first page). Export files are stored, hashed and listed; downloads re-check access. Spreadsheet cells that start with `=`, `+`, `-` or `@` are neutralised.

**Share and schedule.** Share through a workspace (see Collaboration guide) or schedule delivery (Scheduling guide).

**Restricted data.** A report built from restricted project data keeps the same access rules as that project; the report page says so.

**Untrusted text.** Text taken from documents or web pages is stored and shown as data: PredictAI does not act on instructions found inside it, and export cells that look like spreadsheet formulas are neutralised. (Prompt-injection resistance for the language-model features comes from earlier phases and is not re-verified here with a live model.)
