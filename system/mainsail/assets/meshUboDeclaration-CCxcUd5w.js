import{t as e}from"./shaderStore-D-XQlhUT.js";var t=`sceneUboDeclaration`,n=`layout(std140,column_major) uniform;uniform Scene {mat4 viewProjection;
#ifdef MULTIVIEW
mat4 viewProjectionR;
#endif 
mat4 view;mat4 projection;vec4 vEyePosition;};
`;e.IncludesShadersStore[t]||(e.IncludesShadersStore[t]=n);var r=`meshUboDeclaration`,i=`#ifdef WEBGL2
uniform mat4 world;uniform float visibility;
#else
layout(std140,column_major) uniform;uniform Mesh
{mat4 world;float visibility;};
#endif
#define WORLD_UBO
`;e.IncludesShadersStore[r]||(e.IncludesShadersStore[r]=i);