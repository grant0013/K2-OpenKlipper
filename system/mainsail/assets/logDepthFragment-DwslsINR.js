import{t as e}from"./shaderStore-D-XQlhUT.js";var t=`logDepthFragment`,n=`#ifdef LOGARITHMICDEPTH
gl_FragDepthEXT=log2(vFragmentDepth)*logarithmicDepthConstant*0.5;
#endif
`;e.IncludesShadersStore[t]||(e.IncludesShadersStore[t]=n);