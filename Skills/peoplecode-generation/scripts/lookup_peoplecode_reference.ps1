param(
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Name
)

$ErrorActionPreference = "Stop"

function Normalize-Key {
    param([string]$Value)
    return (($Value.Trim() -replace '\s+', ' ').ToLowerInvariant())
}

$names = @($Name | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($names.Count -eq 0) {
    [Console]::Error.WriteLine("Error: at least one name is required.")
    exit 2
}

$referencePath = Join-Path (Split-Path -Parent $PSScriptRoot) "assets\data\peoplecode_reference.json"

try {
    $rawJson = Get-Content -LiteralPath $referencePath -Raw -Encoding UTF8
    $referenceObject = $rawJson | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine("Unable to load PeopleCode reference data.")
    exit 2
}

$referenceMap = @{}
foreach ($property in $referenceObject.PSObject.Properties) {
    $referenceMap[(Normalize-Key $property.Name)] = $property.Value
}

$hasMissing = $false
$isBatchLookup = $names.Count -gt 1

foreach ($requestedName in $names) {
    $key = Normalize-Key $requestedName

    if ($referenceMap.ContainsKey($key)) {
        if ($isBatchLookup) {
            [Console]::Out.WriteLine("===== $requestedName =====")
        }

        [Console]::Out.WriteLine($referenceMap[$key])

        if ($isBatchLookup) {
            [Console]::Out.WriteLine("")
        }
    } else {
        [Console]::Error.WriteLine("No documentation found for '$requestedName'.")
        $hasMissing = $true
    }
}

if ($hasMissing) {
    exit 1
}

exit 0
