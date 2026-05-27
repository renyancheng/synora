import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'models.dart';

class PersistedSessionSnapshot {
  PersistedSessionSnapshot({
    required this.accessToken,
    required this.expiresAt,
    required this.user,
  });

  final String accessToken;
  final DateTime expiresAt;
  final UserProfile user;
}

class LocalSessionStore {
  LocalSessionStore({FlutterSecureStorage? secureStorage})
      : _secureStorage = secureStorage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _secureStorage;

  static const _tokenKey = 'synora.access_token';
  static const _expiresAtKey = 'synora.expires_at';
  static const _userIdKey = 'synora.user_id';
  static const _userEmailKey = 'synora.user_email';
  static const _userDisplayNameKey = 'synora.user_display_name';

  Future<PersistedSessionSnapshot?> readSession() async {
    final accessToken = (await _secureStorage.read(key: _tokenKey))?.trim() ?? '';
    final expiresAtRaw = (await _secureStorage.read(key: _expiresAtKey))?.trim() ?? '';
    final user = await readLastKnownUser();
    if (accessToken.isEmpty || expiresAtRaw.isEmpty || user == null) {
      return null;
    }
    final expiresAt = DateTime.tryParse(expiresAtRaw);
    if (expiresAt == null) {
      return null;
    }
    return PersistedSessionSnapshot(
      accessToken: accessToken,
      expiresAt: expiresAt,
      user: user,
    );
  }

  Future<UserProfile?> readLastKnownUser() async {
    final idRaw = (await _secureStorage.read(key: _userIdKey))?.trim() ?? '';
    final email = (await _secureStorage.read(key: _userEmailKey))?.trim() ?? '';
    final displayName = (await _secureStorage.read(key: _userDisplayNameKey))?.trim() ?? '';
    final id = int.tryParse(idRaw);
    if (id == null || email.isEmpty || displayName.isEmpty) {
      return null;
    }
    return UserProfile(
      id: id,
      email: email,
      displayName: displayName,
    );
  }

  Future<void> saveSession(SessionInfo session) async {
    await _secureStorage.write(key: _tokenKey, value: session.accessToken);
    await _secureStorage.write(key: _expiresAtKey, value: session.expiresAt.toIso8601String());
    await _secureStorage.write(key: _userIdKey, value: session.user.id.toString());
    await _secureStorage.write(key: _userEmailKey, value: session.user.email);
    await _secureStorage.write(key: _userDisplayNameKey, value: session.user.displayName);
  }

  Future<void> clearSessionToken() async {
    await _secureStorage.delete(key: _tokenKey);
    await _secureStorage.delete(key: _expiresAtKey);
  }
}
