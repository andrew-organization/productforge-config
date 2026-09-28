// The Go module pre-commit needs to install this repository's golang hooks
// (checkmake): it builds nothing of its own, and each hook's tool comes
// from its additional_dependencies.
module github.com/andrew-organization/productforge-config

go 1.23
