The goal of this iteration is to convert batches of formal proofs ("formalProof") into main-statement Lean files that contain `sorry`.

Typical workflow:
1. Normalize the JSON files and convert them into corresponding Lean files (InitialJsonConvert).
2. For batches of Lean files, use a local parallel Lean builder to check for compilation errors (LeanCheck). If the original formalProof has issues, fix them (Failed_Lean_to_Json).
3. Use an LLM to process formalProof files in parallel and generate main-statement files, preserving file IDs. After processing, report which files failed to build. For files with compilation errors, roll back the LLM-produced main statement to the original one. The final outputs are: (a) a new set of main-statement Lean files in the `output` folder, and (b) the original JSON files augmented with a `mainStatement` unit (LLM_Agent and FinalJsonConvert).
4. In a visual environment, manually fix files with build errors and verify that the LLM outputs truly meet the task requirements (jsonDisplay).