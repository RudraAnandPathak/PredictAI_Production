"""File parsers. Stdlib-first: CSV/TSV/JSON/XML/XLSX/ODS/DOCX/RTF/TXT/Markdown need no third-party package. PDF text needs
`pypdf` (pure Python); tables and headings in PDFs use `pdfplumber` when installed; scanned pages use the free `tesseract`
and `pdftoppm` programs when present; legacy .xls uses `xlrd` (or an opt-in LibreOffice conversion). Every parser reports
what it could not do instead of silently dropping content."""
