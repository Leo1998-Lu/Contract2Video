# Responsible NLP and media use

Language-grounded video systems can amplify stereotypes, reproduce copyrighted material, or generate deceptive media. Contract2Video exposes a planning and verification workflow; it does not establish that a generated clip is safe, truthful, or suitable for publication.

## Scope and limitations

The contract model can represent entities, actions, temporal and spatial relations, identity, audio, and text, but an observer may miss subtle attributes, cultural context, or harmful implications. A supported constraint means that the configured observer found evidence under its calibration and preprocessing assumptions. It is not a guarantee about real-world truth.

The included scripted observer is a fixture component. It does not perceive video and must not be used for safety claims, human preference claims, or reported benchmark scores. A deployment should add independent observers, uncertainty handling, content policy checks, and human review for high-impact use cases.

## Data and consent

Use licensed or consented training and evaluation media. Document provenance, retention, and access controls. Avoid personal or sensitive data in prompts and logs. Remove identifying metadata before sharing clips. Do not infer identity, demographics, intent, or protected characteristics from generated or observed media without an explicit, validated protocol.

## Disclosure

When a generated video is shared, disclose the generation and editing process where appropriate. Preserve the original prompt, contract, observer mode, and repair trace for audit while minimizing personal information. Keep API credentials and private service responses outside the repository.

## Reporting

Report failure cases, unknown evidence, unresolved hard constraints, and budget exhaustion rather than filtering them out. Separate fixture behavior from learned-model evaluation. Describe observer calibration, human annotation, uncertainty, and statistical procedures so that readers can judge the limits of the claims.
