"""Block junk platform subdomains + clean existing junk brands.

Revision ID: 006
Revises: 005
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Blocklist rules for platform subdomains --
    op.execute("""
        INSERT INTO brand_blocklist (kind, pattern, scope, reason)
        VALUES
            ('block', '*.myshopify.com', 'domain', 'Shopify dev/placeholder stores — real brands use custom domains'),
            ('block', '*.wixsite.com', 'domain', 'Wix default subdomains — not real brand domains'),
            ('block', '*.squarespace.com', 'domain', 'Squarespace default subdomains'),
            ('block', '*.bigcartel.com', 'domain', 'BigCartel default subdomains'),
            ('block', '*.webflow.io', 'domain', 'Webflow default subdomains'),
            ('block', '*.godaddysites.com', 'domain', 'GoDaddy builder subdomains'),
            ('block', '*.netlify.app', 'domain', 'Netlify deploy subdomains'),
            ('block', '*.vercel.app', 'domain', 'Vercel deploy subdomains'),
            ('block', '*.herokuapp.com', 'domain', 'Heroku app subdomains'),
            ('block', '*.carrd.co', 'domain', 'Carrd landing page subdomains'),
            ('block', '*.blogspot.in', 'domain', 'Blogspot subdomains — not D2C brands'),
            ('block', '*.blogspot.com', 'domain', 'Blogspot subdomains — not D2C brands'),
            ('block', '*.tumblr.com', 'domain', 'Tumblr subdomains'),
            ('block', '*.substack.com', 'domain', 'Substack newsletters — not brands')
        ON CONFLICT DO NOTHING
    """)

    # -- Clean up existing junk brands with platform subdomains --
    # Delete signals, dossiers, and brands that are platform subdomains.
    # These were ingested before the filter existed.
    op.execute("""
        WITH junk_brands AS (
            SELECT id FROM brands
            WHERE domain LIKE '%.myshopify.com'
               OR domain LIKE '%.wixsite.com'
               OR domain LIKE '%.squarespace.com'
               OR domain LIKE '%.bigcartel.com'
               OR domain LIKE '%.webflow.io'
               OR domain LIKE '%.netlify.app'
               OR domain LIKE '%.vercel.app'
               OR domain LIKE '%.herokuapp.com'
        )
        DELETE FROM signals WHERE brand_id IN (SELECT id FROM junk_brands)
    """)
    op.execute("""
        WITH junk_brands AS (
            SELECT id FROM brands
            WHERE domain LIKE '%.myshopify.com'
               OR domain LIKE '%.wixsite.com'
               OR domain LIKE '%.squarespace.com'
               OR domain LIKE '%.bigcartel.com'
               OR domain LIKE '%.webflow.io'
               OR domain LIKE '%.netlify.app'
               OR domain LIKE '%.vercel.app'
               OR domain LIKE '%.herokuapp.com'
        )
        DELETE FROM dossiers WHERE brand_id IN (SELECT id FROM junk_brands)
    """)
    op.execute("""
        WITH junk_brands AS (
            SELECT id FROM brands
            WHERE domain LIKE '%.myshopify.com'
               OR domain LIKE '%.wixsite.com'
               OR domain LIKE '%.squarespace.com'
               OR domain LIKE '%.bigcartel.com'
               OR domain LIKE '%.webflow.io'
               OR domain LIKE '%.netlify.app'
               OR domain LIKE '%.vercel.app'
               OR domain LIKE '%.herokuapp.com'
        )
        DELETE FROM approvals WHERE brand_id IN (SELECT id FROM junk_brands)
    """)
    op.execute("""
        WITH junk_brands AS (
            SELECT id FROM brands
            WHERE domain LIKE '%.myshopify.com'
               OR domain LIKE '%.wixsite.com'
               OR domain LIKE '%.squarespace.com'
               OR domain LIKE '%.bigcartel.com'
               OR domain LIKE '%.webflow.io'
               OR domain LIKE '%.netlify.app'
               OR domain LIKE '%.vercel.app'
               OR domain LIKE '%.herokuapp.com'
        )
        DELETE FROM agent_traces WHERE brand_id IN (SELECT id FROM junk_brands)
    """)
    op.execute("""
        DELETE FROM brands
        WHERE domain LIKE '%.myshopify.com'
           OR domain LIKE '%.wixsite.com'
           OR domain LIKE '%.squarespace.com'
           OR domain LIKE '%.bigcartel.com'
           OR domain LIKE '%.webflow.io'
           OR domain LIKE '%.netlify.app'
           OR domain LIKE '%.vercel.app'
           OR domain LIKE '%.herokuapp.com'
    """)

    # -- Also clean brands with auto-generated junk prefixes --
    op.execute("""
        DELETE FROM signals WHERE brand_id IN (
            SELECT id FROM brands WHERE domain ~ '^[0-9]{2,}-[0-9]{2,}[a-z]*-'
        )
    """)
    op.execute("""
        DELETE FROM dossiers WHERE brand_id IN (
            SELECT id FROM brands WHERE domain ~ '^[0-9]{2,}-[0-9]{2,}[a-z]*-'
        )
    """)
    op.execute("""
        DELETE FROM brands WHERE domain ~ '^[0-9]{2,}-[0-9]{2,}[a-z]*-'
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM brand_blocklist
        WHERE pattern IN (
            '*.myshopify.com', '*.wixsite.com', '*.squarespace.com',
            '*.bigcartel.com', '*.webflow.io', '*.godaddysites.com',
            '*.netlify.app', '*.vercel.app', '*.herokuapp.com',
            '*.carrd.co', '*.blogspot.in', '*.blogspot.com',
            '*.tumblr.com', '*.substack.com'
        )
    """)
