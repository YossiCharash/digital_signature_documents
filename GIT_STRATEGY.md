# Git Branching Strategy

This document outlines the git branching strategy for the Invoice Digitalization Platform.

## Branch Structure

### Main Branches

- **`main`** - Production branch
  - Protected branch
  - Only accepts merges from `develop`
  - All commits must pass CI/CD
  - Tagged releases only

- **`develop`** - Development integration branch
  - Main development branch
  - All feature branches merge here
  - Continuous integration runs on every push

### Feature Branches

Feature branches are created from `develop` and follow the naming convention `feature/<feature-name>`:

- **`feature/pdf-reader`** - PDF reading functionality
  - PDF text extraction
  - OCR integration
  - PDF parsing logic

- **`feature/invoice-parser`** - Invoice parsing service
  - Text parsing
  - Data extraction
  - Validation logic

- **`feature/invoice-builder`** - Digital invoice builder
  - Invoice model creation
  - JSON serialization
  - Compliance formatting

- **`feature/digital-signature`** - Digital signing functionality
  - Certificate management
  - PKCS#7 signing
  - Signature verification

- **`feature/email-delivery`** - Email delivery service
  - SMTP integration
  - API-based delivery
  - Email templates

- **`feature/sms-delivery`** - SMS delivery service
  - SMS API integration
  - Phone number normalization
  - Message formatting

- **`feature/api`** - API layer
  - FastAPI routes
  - Request/response handling
  - Error handling

- **`feature/ci-cd`** - CI/CD pipeline
  - GitHub Actions workflows
  - Docker builds
  - Deployment automation

## Workflow

### Creating a Feature Branch

```bash
# Start from develop
git checkout develop
git pull origin develop

# Create and switch to feature branch
git checkout -b feature/my-feature

# Work on feature...
git add .
git commit -m "feat: add feature X"

# Push feature branch
git push origin feature/my-feature
```

### Merging a Feature Branch

1. Ensure feature branch is up to date with `develop`
   ```bash
   git checkout feature/my-feature
   git merge develop
   ```

2. Run tests and linting
   ```bash
   pytest
   black --check app tests
   ruff check app tests
   ```

3. Create pull request to `develop`
   - PR must pass CI
   - Code review required
   - All tests must pass

4. After approval, merge to `develop`

### Releasing to Production

1. Merge `develop` to `main`
   ```bash
   git checkout main
   git merge develop
   git push origin main
   ```

2. Tag the release
   ```bash
   git tag -a v1.0.0 -m "Release version 1.0.0"
   git push origin v1.0.0
   ```

## Commit Message Convention

Follow conventional commits:

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting)
- `refactor:` - Code refactoring
- `test:` - Test additions/changes
- `chore:` - Build process or auxiliary tool changes

Example:
```
feat: add OCR support for scanned PDFs
fix: correct VAT calculation for line items
docs: update API documentation
```

## Branch Protection Rules

### `main` Branch
- Require pull request reviews
- Require status checks to pass
- Require branches to be up to date
- No force pushes
- No deletions

### `develop` Branch
- Require status checks to pass
- Allow force pushes (with caution)
- Allow deletions

## Best Practices

1. **Keep branches focused**: One feature per branch
2. **Regular updates**: Merge `develop` into feature branches regularly
3. **Clean history**: Use rebase to keep history clean (before merging)
4. **Test before merge**: Ensure all tests pass locally
5. **Small commits**: Make frequent, small commits with clear messages
6. **No direct commits to main**: Always use pull requests

## Hotfixes

For critical production fixes:

1. Create hotfix branch from `main`
   ```bash
   git checkout main
   git checkout -b hotfix/critical-fix
   ```

2. Make fix and test
3. Merge to both `main` and `develop`
4. Tag release
