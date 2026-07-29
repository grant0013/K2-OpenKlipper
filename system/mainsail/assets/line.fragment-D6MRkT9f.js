import{t as e}from"./shaderStore-D-XQlhUT.js";import"./clipPlaneFragment-DGqZJNeS.js";import"./logDepthDeclaration-B8O1rFmO.js";import"./logDepthFragment-ccQ8natM.js";var t=`linePixelShader`,n=`#include<clipPlaneFragmentDeclaration>
uniform color: vec4f;
#include<logDepthDeclaration>
#define CUSTOM_FRAGMENT_DEFINITIONS
@fragment
fn main(input: FragmentInputs)->FragmentOutputs {
#define CUSTOM_FRAGMENT_MAIN_BEGIN
#include<logDepthFragment>
#include<clipPlaneFragment>
fragmentOutputs.color=uniforms.color;
#define CUSTOM_FRAGMENT_MAIN_END
}`;e.ShadersStoreWGSL[t]||(e.ShadersStoreWGSL[t]=n);var r={name:t,shader:n};export{r as linePixelShaderWGSL};