# Wallet Persistence Fix

## Problem

Devices were generating **new node identities and wallets on each restart**, causing:

1. **Lost VIBE rewards** - Old wallets abandoned with accumulated rewards
2. **Database pollution** - Multiple node IDs for same device
3. **Network confusion** - Same device appearing as multiple peers

### Example (Alienware)

| Node ID | Wallet | Lifetime | VIBE Lost |
|---------|--------|----------|-----------|
| apn_83e0fa39 | 0x83e0fa39... | 2.6 hrs | 3,140 VIBE |
| apn_6cc33305 | 0x6cc33305... | 24 min | 460 VIBE |
| apn_b2664a6a | 0xb2664a6a... | 9 min | 160 VIBE |

**Total: 3,760 VIBE spread across 3 wallets for same device!**

---

## Root Cause

The original `generate_node_identity()` function had weak error handling:

```python
if identity_file.exists():
    try:
        # Load identity...
    except Exception as e:
        logger.error(f"Failed to load identity: {e}")
        # ⚠️ CONTINUES TO GENERATE NEW IDENTITY

# Generate new identity (silently creates new wallet!)
```

**Issues:**
1. Any error reading identity → Generate new one
2. No backup before modifications
3. No verification after save
4. Silent failures
5. No persistence checks

---

## Solution

### 1. Robust Identity Loading

**New behavior:**
- ✅ Validates required fields exist
- ✅ Creates backup before modifications
- ✅ Verifies saved file can be re-loaded
- ✅ **Fails loudly** if file corrupted (instead of silently creating new)
- ✅ Restores from backup if update fails

### 2. Enhanced Error Handling

**Different errors, different actions:**

| Error Type | Old Behavior | New Behavior |
|------------|--------------|--------------|
| File doesn't exist | Generate new ✓ | Generate new ✓ |
| File corrupted (bad JSON) | Generate new ❌ | **Exit with error** ✓ |
| Missing required fields | Generate new ❌ | **Exit with error** ✓ |
| Permission denied | Generate new ❌ | **Exit with error** ✓ |

### 3. File Verification

```python
# Write identity file
with open(identity_file, 'w') as f:
    json.dump(identity_data, f, indent=2)

# ✅ NEW: Verify it was written correctly
with open(identity_file, 'r') as f:
    verification = json.load(f)
if verification.get('node_id') != node_id:
    raise ValueError("Identity verification failed")
```

### 4. Automatic Backups

```python
# Before modifying existing identity
import shutil
shutil.copy2(identity_file, backup_file)
logger.info(f"Created backup: {backup_file}")
```

### 5. Better Logging

**Before:**
```
INFO: Generated new node identity: apn_xxx
```

**After:**
```
✓ Loaded existing node identity: apn_xxx
✓ Wallet address: 0x...
✓ Identity file: /home/user/.apn/node_identity.json
```

Or on error:
```
❌ CRITICAL: Identity file is corrupted (invalid JSON)
❌ File location: /home/user/.apn/node_identity.json
❌ Please backup/fix the file manually or delete it to generate new identity
❌ WARNING: Deleting will create NEW wallet and LOSE accumulated VIBE!
```

### 6. Secure Permissions

```python
# Ensure config directory has secure permissions
settings.config_dir.chmod(0o700)  # Owner only
identity_file.chmod(0o600)         # Owner read/write only
```

---

## Recovery Tool

Added `recover_wallet.py` to help users:

1. Check current and backup identities
2. View all identity files on system
3. Get instructions for consolidating VIBE
4. Prevent future issues

**Usage:**
```bash
./recover_wallet.py
```

---

## Testing

### Before Deploy (Old Behavior)

```bash
# Simulate identity file corruption
echo "invalid json" > ~/.apn/node_identity.json

# Start APN Core
python main.py

# Result: Silently generates NEW wallet, loses old one ❌
```

### After Deploy (New Behavior)

```bash
# Simulate identity file corruption
echo "invalid json" > ~/.apn/node_identity.json

# Start APN Core
python main.py

# Result:
# ❌ CRITICAL: Identity file is corrupted (invalid JSON)
# ❌ File location: /home/user/.apn/node_identity.json
# ❌ Please backup/fix the file manually or delete it
# ❌ WARNING: Deleting will create NEW wallet and LOSE VIBE!
# [EXIT] ✓
```

---

## Migration

### For Existing Devices

**No action needed!** The fix:
- ✅ Loads existing identities normally
- ✅ Creates backup on first run with new code
- ✅ Prevents future regeneration issues

### For Devices with Lost Wallets

**Option 1: Use most recent wallet**
- Latest identity at `~/.apn/node_identity.json` is automatically used
- Old wallets can be consolidated by network admin

**Option 2: Restore old wallet**
```bash
# If you have backup of old identity
cp /path/to/old_identity.json ~/.apn/node_identity.json
chmod 600 ~/.apn/node_identity.json
```

**Option 3: Consolidate VIBE**
- Contact network admin with list of orphaned wallets
- VIBE can be transferred to current wallet

---

## Best Practices

### For Users

1. **Backup identity after first run:**
   ```bash
   cp ~/.apn/node_identity.json ~/apn_identity_backup_$(date +%Y%m%d).json
   ```

2. **Check identity before starting:**
   ```bash
   cat ~/.apn/node_identity.json | grep node_id
   ```

3. **Monitor your node_id:**
   - Should stay constant across restarts
   - If it changes, identity was regenerated (bad!)

### For Developers

1. **Never silently create new identity** - Fail loudly instead
2. **Always backup before modifications** - Can restore if update fails
3. **Always verify after save** - Ensure file is readable and correct
4. **Log file paths clearly** - Help users find/fix issues
5. **Use appropriate permissions** - Protect sensitive key material

---

## Files Modified

- `apn_server.py` - Updated `generate_node_identity()` function
- `core/settings.py` - Enhanced `ensure_config_dir()` with permissions
- `recover_wallet.py` - **NEW** - Recovery tool for lost wallets
- `WALLET-PERSISTENCE-FIX.md` - **NEW** - This documentation

---

## Verification

After updating, verify the fix works:

```bash
# 1. Note your current node_id and wallet
cat ~/.apn/node_identity.json

# 2. Restart APN Core
./launch.sh

# 3. Verify same identity loaded
# Look for: "✓ Loaded existing node identity: apn_xxx"

# 4. Check backup was created
ls -la ~/.apn/node_identity.json.backup

# 5. Restart again to ensure persistence
./launch.sh

# 6. Verify SAME node_id (not new one)
```

---

**Status:** ✅ Fixed and deployed
**Version:** 2.0.1
**Date:** 2026-02-08
**Impact:** Prevents wallet loss on all future devices
