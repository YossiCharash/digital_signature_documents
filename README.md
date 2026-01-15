# Invoice Digitalization Platform – Text PDF Only (Israel)

A production-grade system for converting **text-based invoice PDFs** into legally compliant digital invoices in accordance with Israeli Tax Authority requirements.

## ⚠️ Important Limitation

**This system only supports text-based (born-digital) PDF invoices.**

- ✅ **Supported**: PDFs with embedded, selectable text
- ❌ **Not Supported**: Scanned PDFs, image-only PDFs, or PDFs without embedded text
- ❌ **No OCR**: This system does not perform optical character recognition
- ❌ **No Image Processing**: Image-based PDFs will be rejected with a clear error message

If you submit a scanned or image-only PDF, the system will return an error indicating that only text-based PDFs are supported.

## Overview

This platform provides a complete solution for:
- Extracting invoice data from **text-based PDF files only** (embedded text extraction)
- Parsing and structuring invoice information
- Building Israeli Tax Authority-compliant digital invoices
- Digitally signing invoices using PKI/X.509 certificates
- Delivering signed invoices via Email or SMS

## Architecture

The system follows **Clean Architecture** principles with clear separation of concerns:

```
app/
├── api/          # FastAPI routes and endpoints
├── core/         # Core business logic (security, signing)
├── models/       # Data models (Pydantic)
├── services/     # Business services (PDF, parsing, delivery)
└── utils/        # Utilities (logging, validation)
```

### Key Components

1. **PDF Reader Service** (`app/services/pdf_reader.py`)
   - **Text-based PDFs only** - extracts embedded text from PDFs
   - Rejects image-only/scanned PDFs with clear error messages
   - Uses pdfplumber and PyPDF2 for text extraction
   - No OCR or image processing capabilities

2. **Invoice Parser** (`app/services/invoice_parser.py`)
   - Extracts invoice number, dates, supplier/customer info
   - Parses line items, VAT, and totals
   - Validates mandatory fields

3. **Invoice Builder** (`app/services/invoice_builder.py`)
   - Creates deterministic JSON structure
   - Ensures numeric precision
   - Prepares for future allocation number integration

4. **Digital Signer** (`app/core/signing.py`)
   - SHA-256 hashing
   - PKCS#7/CMS signature format
   - X.509 certificate-based signing
   - Tamper detection

5. **Delivery Services**
   - **Email Service**: SMTP or API-based delivery
   - **SMS Service**: REST API-based notifications

## Technology Stack

- **Python 3.12**
- **FastAPI** - Modern async web framework
- **Pydantic** - Data validation and settings
- **Cryptography** - Digital signatures and PKI
- **PyPDF2 / pdfplumber** - PDF text extraction (text-based PDFs only)
- **pytest** - Testing framework
- **Docker** - Containerization

## Security Model

### Security Features

- **TLS Enforcement**: All communications use TLS
- **Environment-based Configuration**: No secrets in code
- **Secure Key Management**: Private keys loaded only from secure files
- **Immutable Signatures**: Signed invoices cannot be modified
- **No Sensitive Data in Logs**: Logging excludes sensitive information

### Certificate Management

- X.509 certificates for digital signing
- Private keys stored securely (never in code)
- Certificate validation on startup
- Support for password-protected keys

## Regulatory Compliance (Israel)

### Israeli Tax Authority Requirements

The system is designed to comply with Israeli Tax Authority regulations:

- **Business ID Validation**: Israeli business ID format validation (9 digits with checksum)
- **VAT Handling**: Proper VAT calculation and reporting
- **Digital Invoice Format**: JSON-based structure ready for tax authority integration
- **Allocation Number Support**: Structure prepared for future allocation number integration
- **7-Year Archival**: System designed for long-term invoice storage

### Legal Disclaimer

**IMPORTANT**: This system provides the technical infrastructure for digital invoice creation and signing. Legal compliance depends on:

1. **Certificate Authority Approval**: The X.509 certificate used for signing must be issued by a certificate authority recognized by the Israeli Tax Authority
2. **Tax Authority Approval**: The digital invoice format and signing process must be approved by the Israeli Tax Authority
3. **Legal Review**: Consult with legal and tax professionals to ensure full compliance with current regulations

The system is designed to be compliant-ready but requires proper certification and approval for production use.

## Development Workflow

### Git Branching Strategy

The repository uses the following branch structure:

- **main** - Production branch (protected)
- **develop** - Development integration branch
- **feature/*** - Feature branches:
  - `feature/pdf-reader`
  - `feature/invoice-parser`
  - `feature/invoice-builder`
  - `feature/digital-signature`
  - `feature/email-delivery`
  - `feature/sms-delivery`
  - `feature/api`
  - `feature/ci-cd`

### Development Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd invoice-digitalization
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -e ".[dev]"
   ```

   **Note**: No additional system dependencies are required. The system only processes text-based PDFs and does not require OCR libraries.

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

6. **Generate signing keys** (for development)
   ```bash
   python scripts/generate_keys.py --output-dir ./certificates
   ```

7. **Run the application**
   ```bash
   uvicorn app.main:app --reload
   ```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_pdf_reader.py
```

### Code Quality

```bash
# Format code
black app tests

# Lint code
ruff check app tests

# Type checking
mypy app
```

## Docker Deployment

### Build Docker Image

```bash
docker build -f docker/Dockerfile -t invoice-digitalization .
```

### Run with Docker Compose

```bash
cd docker
docker-compose up -d
```

The API will be available at `http://localhost:8000`

### Environment Variables

Configure via `.env` file or environment variables. See `.env.example` for all available options.

## API Endpoints

### Upload Invoice

```http
POST /api/v1/invoices/upload
Content-Type: multipart/form-data

file: <PDF file>
```

**Response:**
```json
{
  "invoice_id": "uuid",
  "invoice_number": "INV-001",
  "status": "processed",
  "signed_invoice": { ... }
}
```

### Send Invoice

```http
POST /api/v1/invoices/send
Content-Type: application/x-www-form-urlencoded

invoice_id: <invoice_id>
delivery_method: email|sms
recipient: <email_or_phone>
subject: <optional>
message: <optional>
```

### Health Check

```http
GET /health
```

## CI/CD

### Continuous Integration

The CI pipeline (`/.github/workflows/ci.yml`) runs on:
- Push to `main` or `develop`
- Pull requests

**CI Steps:**
1. Lint code (black, ruff, mypy)
2. Run tests with coverage
3. Upload coverage reports

### Continuous Deployment

The CD pipeline (`/.github/workflows/cd.yml`) runs on:
- Push to `main`
- Version tags (`v*`)

**CD Steps:**
1. Build Docker image
2. Tag image with version/sha
3. Push to Docker registry (if configured)

## Project Structure

```
invoice-digitalization/
├── app/
│   ├── api/              # API routes
│   ├── core/             # Core business logic
│   ├── models/           # Data models
│   ├── services/         # Business services
│   ├── utils/            # Utilities
│   ├── config.py         # Configuration
│   └── main.py           # FastAPI app
├── tests/
│   ├── unit/             # Unit tests
│   └── integration/      # Integration tests
├── scripts/              # Utility scripts
├── docker/               # Docker configuration
├── .github/workflows/    # CI/CD workflows
├── pyproject.toml        # Project configuration
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## Contributing

1. Create a feature branch from `develop`
2. Make your changes
3. Write/update tests
4. Ensure all tests pass
5. Run linting and type checking
6. Submit a pull request to `develop`

## License

Proprietary - All rights reserved

## Support

For issues and questions, please contact the development team.

---

**Version**: 1.0.0  
**Last Updated**: 2024
