# Deployment Migration Guide

## Problem

When deploying to Google Cloud (or any cloud platform), you may encounter HTTP 500 errors because the database schema is missing the new columns:
- `bot_stop_loss_enabled`
- `tv_signal_close_enabled`

## Solution

The application now **automatically migrates** the database on startup. The `init_db()` function will:
1. Create tables if they don't exist
2. Add missing columns if tables exist but columns are missing

## Automatic Migration (Recommended)

**No action required!** The migration runs automatically when the app starts.

The `init_db()` function in `db.py` now includes automatic migration that:
- Detects if columns are missing
- Adds them with appropriate defaults
- Works with SQLite, PostgreSQL, MySQL, and other databases

## Manual Migration (If Needed)

If you prefer to run migrations manually before deploying, you can use the migration script:

### For SQLite (Local Development)
```bash
cd app/tv-binance-bot
python migrate_add_mechanism_flags.py
```

### For PostgreSQL/MySQL/Cloud SQL (Production)
```bash
cd app/tv-binance-bot
python migrate_add_mechanism_flags_sqlalchemy.py
```

## Google Cloud Deployment

### Option 1: Automatic Migration (Recommended)

1. Deploy your updated code
2. The app will automatically add missing columns on startup
3. Check logs to confirm migration completed

### Option 2: Manual SQL Migration

If you have direct database access, you can run SQL directly:

**For PostgreSQL (Cloud SQL):**
```sql
ALTER TABLE positions 
ADD COLUMN IF NOT EXISTS bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE positions 
ADD COLUMN IF NOT EXISTS tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE;
```

**For MySQL:**
```sql
ALTER TABLE positions 
ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE positions 
ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE;
```

## Environment Variables

If you're using Cloud SQL or another database, set the `DATABASE_URL` environment variable:

```bash
# PostgreSQL (Cloud SQL)
export DATABASE_URL="postgresql://user:password@/dbname?host=/cloudsql/project:region:instance"

# MySQL
export DATABASE_URL="mysql://user:password@host:port/dbname"

# SQLite (default, for local development)
export DATABASE_URL="sqlite:///./trading_bot.db"
```

## Verification

After deployment, verify the migration worked:

1. Check application logs for "資料庫初始化完成" (Database initialization complete)
2. Query the database to verify columns exist:
   ```sql
   SELECT column_name 
   FROM information_schema.columns 
   WHERE table_name = 'positions' 
   AND column_name IN ('bot_stop_loss_enabled', 'tv_signal_close_enabled');
   ```
3. Test the API endpoint:
   ```bash
   curl http://your-app-url/positions
   ```

## Troubleshooting

### Error: "column does not exist"

**Cause**: Migration didn't run or failed silently.

**Solution**:
1. Check application logs for migration errors
2. Run manual migration script
3. Or run SQL directly (see Option 2 above)

### Error: "syntax error" during migration

**Cause**: Database-specific SQL syntax issue.

**Solution**:
1. Check which database you're using
2. Run database-specific SQL manually
3. Or update the migration function in `db.py` for your database type

### Error: "permission denied"

**Cause**: Database user doesn't have ALTER TABLE permissions.

**Solution**:
1. Grant ALTER TABLE permission to your database user
2. Or run migration as database admin

## Notes

- All existing positions will have both flags set to `True` (backward compatible)
- New positions will also default to `True` for both flags
- The migration is idempotent - safe to run multiple times
- Migration runs automatically on every app startup (checks if columns exist first)

