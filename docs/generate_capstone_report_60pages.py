#!/usr/bin/env python3
"""
generate_capstone_report_60pages.py

Master Build Script for the 60+ Page BCSE497J Capstone Project Report.
Assembles all preliminary front matter, 7 technical chapters matching sample-reference.docx,
APA references, and 4 technical appendices into a single, high-fidelity Microsoft Word (.docx) document,
compiles it to PDF via headless LibreOffice, and audits the exact page count using pdfinfo.
Saves the final deliverables to docs/review-docs/ and docs/.
"""

import os
import sys
import shutil
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, "src_report")
REVIEW_DOCS_DIR = os.path.join(SCRIPT_DIR, "review-docs")
os.makedirs(REVIEW_DOCS_DIR, exist_ok=True)
sys.path.insert(0, SRC_DIR)

# Import figure generator to guarantee all 23 figures are fresh
from generate_all_figures import (
    make_fig01, make_fig02, make_fig03, make_fig04, make_fig05,
    make_fig06, make_fig07, make_fig08, make_fig09, make_fig10,
    make_fig11, make_fig12, make_fig13, make_fig14, make_fig15,
    make_fig16, make_fig17, make_fig18, make_fig19, make_fig20,
    make_fig21, make_fig22, make_fig23
)

from report_styles import init_document, add_body_section
from preliminary_pages import add_preliminary_pages
from chapter1_intro import add_chapter_1
from chapter2_lit_review import add_chapter_2
from chapter3_math_model import add_chapter_3
from chapter4_architecture import add_chapter_4
from chapter5_experimental_setup import add_chapter_5
from chapter6_results import add_chapter_6
from chapter7_conclusion import add_chapter_7
from references_and_appendices import add_references_and_appendices


def build_report():
    print("==============================================================================")
    print(" BUILDING BCSE497J CAPSTONE PROJECT MASTER REPORT (TARGET: 60+ PAGES)")
    print(" Exact Style, Layout, Figures, Formulas & Algorithms matching sample-reference.docx")
    print("==============================================================================")
    
    # Step 0: Ensure all 23 figures are generated
    print("\n[0/10] Verifying and Generating All 23 Publication-Grade 300 DPI Figures...")
    make_fig01(); make_fig02(); make_fig03(); make_fig04(); make_fig05()
    make_fig06(); make_fig07(); make_fig08(); make_fig09(); make_fig10()
    make_fig11(); make_fig12(); make_fig13(); make_fig14(); make_fig15()
    make_fig16(); make_fig17(); make_fig18(); make_fig19(); make_fig20()
    make_fig21(); make_fig22(); make_fig23()
    print("All 23 figures ready.")

    # 1. Initialize Document
    print("\n[1/10] Initializing A4 Document Geometry & Roman Numeral Preliminary Section...")
    doc = init_document()
    
    # 2. Add Preliminary Pages (Cover with Seal, Title, Declaration, Certificate, Abstract, Acknowledgement, TOC, LOT, LOF, Nom)
    print("[2/10] Generating Preliminary Front Matter Pages (VIT University Seal, TOC, LOT, LOF, Symbols)...")
    add_preliminary_pages(doc)
    
    # 3. Add Body Section with Arabic Decimal Pagination
    print("[3/10] Creating Main Body Section with Arabic Page Numbering & 1.5in Gutter...")
    add_body_section(doc)
    
    # 4. Chapters 1 through 7
    print("[4/10] Assembling Chapter 1: Introduction (Objectives Mapped to Deliverables Table 3)...")
    add_chapter_1(doc)
    
    print("[5/10] Assembling Chapter 2: Literature Review & Background (Timeline Fig 1, Taxonomy Fig 2, Landscape Table 5)...")
    add_chapter_2(doc)
    
    print("[6/10] Assembling Chapter 3: Problem Formulation & Architecture (Hydrodynamics, Encirclement, FDI, CBF, Algorithms 1 & 2)...")
    add_chapter_3(doc)
    
    print("[7/10] Assembling Chapter 4: System Design & Implementation (Dual-Loop Fig 5, Comms Fig 10, Node Graph Fig 11, Provenance)...")
    add_chapter_4(doc)
    
    print("[8/10] Assembling Chapter 5: Experimental Setup & Evaluation Methodology (Scenarios, Wilson Intervals, Hyperparameters)...")
    add_chapter_5(doc)
    
    print("[9/10] Assembling Chapter 6: Results, Analysis & Discussion (Landscape Table 15, Figures 13-23, Tables 16-23)...")
    add_chapter_6(doc)
    
    print("[10/10] Assembling Chapter 7: Conclusion & Future Work + References & Appendices A-D...")
    add_chapter_7(doc)
    add_references_and_appendices(doc)
    
    # 5. Save DOCX in review-docs and root docs
    docx_out_path = os.path.join(REVIEW_DOCS_DIR, "BCSE497J_Capstone_Project_Report_60Pages.docx")
    print(f"\nSaving Master Word Document: {docx_out_path}")
    doc.save(docx_out_path)
    
    # Also copy to docs/ root
    docx_root_path = os.path.join(SCRIPT_DIR, "BCSE497J_Capstone_Project_Report_60Pages.docx")
    shutil.copyfile(docx_out_path, docx_root_path)
    print("Word Document successfully created and copied to both review-docs/ and docs/!")
    
    # 6. Compile to PDF via LibreOffice
    print("\nCompiling Document to High-Fidelity PDF via Headless LibreOffice...")
    cmd_pdf = [
        "libreoffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", REVIEW_DOCS_DIR,
        docx_out_path
    ]
    res = subprocess.run(cmd_pdf, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR compiling to PDF: {res.stderr}")
        return False, 0
    
    pdf_out_path = os.path.join(REVIEW_DOCS_DIR, "BCSE497J_Capstone_Project_Report_60Pages.pdf")
    pdf_root_path = os.path.join(SCRIPT_DIR, "BCSE497J_Capstone_Project_Report_60Pages.pdf")
    shutil.copyfile(pdf_out_path, pdf_root_path)
    print(f"PDF Document generated: {pdf_out_path}")
    
    # 7. Audit Page Count
    print("\nAuditing Final Page Count via pdfinfo...")
    res_info = subprocess.run(["pdfinfo", pdf_out_path], capture_output=True, text=True)
    page_count = None
    for line in res_info.stdout.splitlines():
        if "Pages:" in line:
            page_count = int(line.split(":")[1].strip())
            break
            
    print("==============================================================================")
    print(f" MASTER DELIVERABLE AUDIT: FINAL PAGE COUNT = {page_count} PAGES")
    print("==============================================================================")
    
    if page_count and page_count >= 60:
        print(f"SUCCESS: Page count ({page_count}) satisfies and exceeds the 60+ page mandate!")
        return True, page_count
    else:
        print(f"WARNING: Page count ({page_count}) is under the 60-page threshold.")
        return False, page_count


if __name__ == "__main__":
    success, count = build_report()
    if not success:
        sys.exit(1)
