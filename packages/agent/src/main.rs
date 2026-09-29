fn banner() -> String {
    format!("jatszoter-agent {}", env!("CARGO_PKG_VERSION"))
}

fn main() {
    println!("{}", banner());
}

#[cfg(test)]
mod tests {
    use super::banner;

    #[test]
    fn banner_names_the_crate_and_version() {
        assert_eq!(
            banner(),
            format!("jatszoter-agent {}", env!("CARGO_PKG_VERSION"))
        );
    }
}
