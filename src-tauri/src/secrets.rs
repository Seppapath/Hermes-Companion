pub const API_TOKEN_SERVICE: &str = "com.hermes.companion.api-token";

pub struct TokenPersistence {
    pub config_token: String,
    pub keyring_service: String,
    pub keyring_account: String,
}

pub fn load_api_token(client_id: &str, fallback: &str) -> String {
    if !fallback.trim().is_empty() || client_id.trim().is_empty() {
        return fallback.to_string();
    }

    match entry(client_id).and_then(|entry| entry.get_password().map_err(|error| error.to_string()))
    {
        Ok(token) => token,
        Err(_) => String::new(),
    }
}

pub fn persist_api_token(client_id: &str, token: &str) -> TokenPersistence {
    if client_id.trim().is_empty() {
        return TokenPersistence {
            config_token: token.to_string(),
            keyring_service: String::new(),
            keyring_account: String::new(),
        };
    }

    let trimmed = token.trim();
    if trimmed.is_empty() {
        let _ = clear_api_token(client_id);
        return TokenPersistence {
            config_token: String::new(),
            keyring_service: API_TOKEN_SERVICE.into(),
            keyring_account: client_id.to_string(),
        };
    }

    match entry(client_id).and_then(|entry| {
        entry
            .set_password(trimmed)
            .map_err(|error| error.to_string())
    }) {
        Ok(()) => TokenPersistence {
            config_token: String::new(),
            keyring_service: API_TOKEN_SERVICE.into(),
            keyring_account: client_id.to_string(),
        },
        Err(_) => TokenPersistence {
            config_token: trimmed.to_string(),
            keyring_service: String::new(),
            keyring_account: String::new(),
        },
    }
}

fn clear_api_token(client_id: &str) -> Result<(), String> {
    let entry = entry(client_id)?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

fn entry(client_id: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(API_TOKEN_SERVICE, client_id).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{load_api_token, persist_api_token};
    use uuid::Uuid;

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    #[test]
    fn round_trips_token_via_secure_storage() {
        let client_id = format!("test-client-{}", Uuid::new_v4());
        let token = format!("token-{}", Uuid::new_v4());

        let persisted = persist_api_token(&client_id, &token);
        assert!(persisted.config_token.is_empty());
        assert_eq!(load_api_token(&client_id, ""), token);

        let cleared = persist_api_token(&client_id, "");
        assert!(cleared.config_token.is_empty());
        assert!(load_api_token(&client_id, "").is_empty());
    }
}
