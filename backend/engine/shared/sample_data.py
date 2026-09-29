import csv
import io

SAMPLE_ASSESSEE = {"assessee_name": "Meridian Consulting LLP", "assessee_pan": "AAFFM1234K", "financial_year": "2024-25"}

_BOOKS_HEADER = ["transaction_id", "customer_name", "customer_code", "party_pan", "party_gstin", "document_number", "document_date", "taxable_value", "gst_value", "tds_expected", "section", "advance", "financial_year"]
_BOOKS = [
    # C001 ABC Ltd — one books ₹10,000 vs two 26AS (₹4,000 + ₹6,000) from two TANs, Q1 → GROUP_SAME_QUARTER
    ["INV008", "ABC Ltd", "C001", "AAACA1234A", "27AAACA1234A1Z5", "INV/24-25/008", "15-04-2024", "100000", "18000", "10000", "194J", "N", "2024-25"],
    # C002 exact 1:1 same quarter
    ["INV001", "Bharat Steel Industries Pvt Ltd", "C002", "AABCB5678B", "24AABCB5678B1Z2", "INV/24-25/001", "10-04-2024", "250000", "45000", "5000", "194C", "N", "2024-25"],
    # C003 exact 1:1 outside quarter (books Q1, 26AS Q2)
    ["INV002", "Coastal Logistics Ltd", "C003", "AACCC9012C", "", "INV/24-25/002", "28-06-2024", "150000", "27000", "3000", "194C", "N", "2024-25"],
    # C004 many books → one 26AS (Q2)
    ["INV003", "Deccan Pharma Ltd", "C004", "AADCD3456D", "36AADCD3456D1Z9", "INV/24-25/003", "05-07-2024", "60000", "10800", "6000", "194J", "N", "2024-25"],
    ["INV004", "Deccan Pharma Ltd", "C004", "AADCD3456D", "36AADCD3456D1Z9", "INV/24-25/004", "19-08-2024", "40000", "7200", "4000", "194J", "N", "2024-25"],
    ["INV005", "Deccan Pharma Ltd", "C004", "AADCD3456D", "36AADCD3456D1Z9", "INV/24-25/005", "02-09-2024", "20000", "3600", "2000", "194J", "N", "2024-25"],
    # C005 group outside quarter (books Q3 ₹7,000 + ₹8,000 vs one 26AS Q4 ₹15,000)
    ["INV006", "Everest Infra Projects Ltd", "C005", "AAECE7890E", "", "INV/24-25/006", "12-11-2024", "350000", "63000", "7000", "194C", "N", "2024-25"],
    ["INV007", "Everest Infra Projects Ltd", "C005", "AAECE7890E", "", "INV/24-25/007", "20-12-2024", "400000", "72000", "8000", "194C", "N", "2024-25"],
    # C006 amount mismatch (books 12,000 vs 26AS 10,800)
    ["INV009", "Falcon Media Services", "C006", "AAFCF2345F", "", "INV/24-25/009", "08-05-2024", "120000", "21600", "12000", "194J", "N", "2024-25"],
    # C007 missing in 26AS
    ["INV010", "Ganga Textiles Pvt Ltd", "C007", "AAGCG6789G", "09AAGCG6789G1Z1", "INV/24-25/010", "22-05-2024", "80000", "14400", "1600", "194C", "N", "2024-25"],
    # C009 Sunrise — TAN not in master, deductor name similar → REVIEW_REQUIRED (confirm in Identity Review)
    ["INV011", "Sunrise Textile Mills Pvt Ltd", "C009", "AASCS4567S", "", "INV/24-25/011", "14-10-2024", "90000", "16200", "9000", "194J", "N", "2024-25"],
    # C010 duplicate books entry
    ["INV012", "Harbour Shipping Co", "C010", "AAHCH1122H", "", "INV/24-25/012", "03-06-2024", "50000", "9000", "1000", "194C", "N", "2024-25"],
    ["INV012A", "Harbour Shipping Co", "C010", "AAHCH1122H", "", "INV/24-25/012", "03-06-2024", "50000", "9000", "1000", "194C", "N", "2024-25"],
    # C011 matched but not claimable (26AS status U)
    ["INV013", "Indus Retail Ltd", "C011", "AAICI3344I", "", "INV/24-25/013", "18-01-2025", "200000", "36000", "4000", "194C", "N", "2024-25"],
    # C012 section preference — two 26AS entries of ₹2,500 (194C and 194J); books 194J
    ["INV014", "Jupiter Advisory LLP", "C012", "AAJFJ5566J", "", "INV/24-25/014", "25-02-2025", "25000", "4500", "2500", "194J", "N", "2024-25"],
    ["INV015", "Jupiter Advisory LLP", "C012", "AAJFJ5566J", "", "INV/24-25/015", "26-02-2025", "25000", "4500", "2500", "194C", "N", "2024-25"],
    # C013 tolerance (books 5,000 vs 26AS 4,995)
    ["INV016", "Kaveri Engineering Works", "C013", "AAKCK7788K", "", "INV/24-25/016", "11-03-2025", "500000", "90000", "5000", "194C", "Y", "2024-25"],
    # C014 large amounts
    ["INV017", "Lotus Realty Developers Ltd", "C014", "AALCL9900L", "27AALCL9900L1Z7", "INV/24-25/017", "30-09-2024", "125000000", "22500000", "1250000", "194J", "N", "2024-25"],
]

_STMT_HEADER = ["tan", "deductor_name", "transaction_date", "tax_deducted", "tds_deposited", "status", "section", "amount_paid", "financial_year"]
_STMTS = [
    ["ABCD12345E", "ABC LIMITED", "20-04-2024", "4000", "4000", "F", "194J", "40000", "2024-25"],
    ["XYZB67890C", "ABC LIMITED - MUMBAI BRANCH", "22-04-2024", "6000", "6000", "F", "194J", "60000", "2024-25"],
    ["MUMB11111A", "BHARAT STEEL INDUSTRIES PRIVATE LIMITED", "12-04-2024", "5000", "5000", "F", "194C", "250000", "2024-25"],
    ["MUMB11111A", "BHARAT STEEL INDUSTRIES PRIVATE LIMITED", "12-04-2024", "5000", "5000", "F", "194C", "250000", "2024-25"],
    ["CHEN22222B", "COASTAL LOGISTICS LTD", "05-07-2024", "3000", "3000", "F", "194C", "150000", "2024-25"],
    ["HYDD33333C", "DECCAN PHARMA LIMITED", "30-09-2024", "12000", "12000", "F", "194J", "120000", "2024-25"],
    ["DELE44444D", "EVEREST INFRA PROJECTS LIMITED", "10-01-2025", "15000", "15000", "F", "194C", "750000", "2024-25"],
    ["BLRF55555E", "FALCON MEDIA SERVICES", "15-05-2024", "10800", "10800", "F", "194J", "108000", "2024-25"],
    ["PUNH66666G", "HORIZON DIGITAL PVT LTD", "09-08-2024", "2200", "2200", "F", "194J", "22000", "2024-25"],
    ["KOLS77777H", "SUNRISE TEXTILES PRIVATE LIMITED", "20-10-2024", "9000", "9000", "F", "194J", "90000", "2024-25"],
    ["AHMH88888I", "HARBOUR SHIPPING COMPANY", "10-06-2024", "1000", "1000", "F", "194C", "50000", "2024-25"],
    ["JAII99999J", "INDUS RETAIL LIMITED", "25-01-2025", "4000", "4000", "U", "194C", "200000", "2024-25"],
    ["LKOJ12121K", "JUPITER ADVISORY LLP", "28-02-2025", "2500", "2500", "F", "194C", "25000", "2024-25"],
    ["LKOJ12121K", "JUPITER ADVISORY LLP", "28-02-2025", "2500", "2500", "F", "194J", "25000", "2024-25"],
    ["NAGK34343L", "KAVERI ENGINEERING WORKS", "15-03-2025", "4995", "4995", "F", "194C", "499500", "2024-25"],
    ["MUML56565M", "LOTUS REALTY DEVELOPERS LTD", "03-10-2024", "1250000", "1250000", "F", "194J", "125000000", "2024-25"],
    ["RAND78787N", "RANDOM TRADERS AND SUPPLIERS", "11-11-2024", "1500", "1500", "F", "194C", "15000", "2024-25"],
]

_MASTER_HEADER = ["customer_code", "customer_name", "pan", "gstin", "tan", "aliases"]
_MASTER = [
    ["C001", "ABC Ltd", "AAACA1234A", "27AAACA1234A1Z5", "ABCD12345E;XYZB67890C", "ABC Limited"],
    ["C002", "Bharat Steel Industries Pvt Ltd", "AABCB5678B", "24AABCB5678B1Z2", "MUMB11111A", ""],
    ["C003", "Coastal Logistics Ltd", "AACCC9012C", "", "CHEN22222B", ""],
    ["C004", "Deccan Pharma Ltd", "AADCD3456D", "36AADCD3456D1Z9", "HYDD33333C", ""],
    ["C005", "Everest Infra Projects Ltd", "AAECE7890E", "", "DELE44444D", ""],
    ["C006", "Falcon Media Services", "AAFCF2345F", "", "BLRF55555E", ""],
    ["C007", "Ganga Textiles Pvt Ltd", "AAGCG6789G", "09AAGCG6789G1Z1", "", ""],
    ["C008", "Horizon Digital Pvt Ltd", "AAHCH9876H", "", "PUNH66666G", ""],
    ["C009", "Sunrise Textile Mills Pvt Ltd", "AASCS4567S", "", "", ""],
    ["C010", "Harbour Shipping Co", "AAHCH1122H", "", "", "Harbour Shipping Company"],
    ["C011", "Indus Retail Ltd", "AAICI3344I", "", "JAII99999J", ""],
    ["C012", "Jupiter Advisory LLP", "AAJFJ5566J", "", "LKOJ12121K", ""],
    ["C013", "Kaveri Engineering Works", "AAKCK7788K", "", "NAGK34343L", ""],
    ["C014", "Lotus Realty Developers Ltd", "AALCL9900L", "27AALCL9900L1Z7", "", ""],
]


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def sample_files():
    return {
        "books": ("sample_books_2024-25.csv", _csv(_BOOKS_HEADER, _BOOKS)),
        "form26as": ("sample_26as_2024-25.csv", _csv(_STMT_HEADER, _STMTS)),
        "customer_master": ("sample_customer_master.csv", _csv(_MASTER_HEADER, _MASTER)),
    }
