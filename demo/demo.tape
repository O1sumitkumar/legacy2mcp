# legacy2mcp demo recording - driven by VHS (https://github.com/charmbracelet/vhs)
#
#   brew install vhs
#   vhs demo/demo.tape        # run from the repo root -- needs network access
#                             # to the public Swagger Petstore demo API
#
# Produces demo/legacy2mcp.gif and demo/legacy2mcp.mp4.
# Keep every Type line ASCII-only: VHS mistypes non-ASCII characters.

Output demo/legacy2mcp.gif
Output demo/legacy2mcp.mp4

Set Shell "bash"
Set FontSize 16
Set Width 1280
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms
Set Margin 0

# --- setup (hidden): prompt, PATH, clean slate ---
Hide
Type "export PS1='$ '"
Enter
Type "[ -d .venv ] && export PATH=$PWD/.venv/bin:$PATH"
Enter
Type "rm -f config.yaml && clear"
Enter
Sleep 1s
Show

Type "# legacy2mcp: turn a WSDL, database, or REST API into safe, typed MCP tools"
Sleep 800ms
Enter
Sleep 1s

Type "# 1. Point it at a live REST API -- it finds the OpenAPI spec itself"
Enter
Sleep 400ms
Type "legacy2mcp init --rest-url https://petstore3.swagger.io/api/v3 --out config.yaml"
Enter
Sleep 4s

Type "# 2. Introspect -> one typed MCP tool per endpoint, read-only by default"
Enter
Sleep 400ms
Type "legacy2mcp inspect --config config.yaml | jq 'map(.name)'"
Enter
Sleep 3s

Type "# 3. Each tool carries a real JSON Schema, built from the live spec"
Enter
Sleep 400ms
Type "legacy2mcp inspect --config config.yaml | jq '.[2]'"
Enter
Sleep 3500ms

Type "# deletePet is in the spec too, but stays hidden: it's a write."
Enter
Type "# Every call is schema-validated. Every call is audit-logged."
Enter
Sleep 2500ms

Hide
Type "rm -f config.yaml"
Enter
Show
Sleep 500ms
